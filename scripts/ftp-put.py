#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hang-proof FTP upload: success = remote SIZE == local size (not 226).

Exit codes:
  0 = size match (success)
  1 = size mismatch / incomplete / transfer error
  2 = connect/login/cwd error
  3 = timeout
"""
from __future__ import print_function

import argparse
import json
import os
import signal
import socket
import sys
import time

try:
    from ftplib import FTP, error_perm, error_temp
except ImportError:
    print("HATA: ftplib yok", file=sys.stderr)
    sys.exit(2)

STATE_PATH = "/yedek/config/ftp-upload.state"
DEFAULT_LOG = "/yedek/config/ftp-upload.log"
PROGRESS_INTERVAL = 30


def eprint(*args):
    print(*args, file=sys.stderr)


def log_line(path, msg):
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(path, "a") as fh:
            fh.write(line)
    except (OSError, IOError):
        pass
    # stderr only — stdout must stay clean for shell $(sendftpfile) capture
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except (OSError, IOError):
        pass


def timeout_sec_for_size(local_size):
    size_mb = max(1, int((local_size + 1048575) // 1048576))
    return int(min(14400, max(1800, size_mb * 3)))


class OverallTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise OverallTimeout("overall timeout")


def write_state(payload):
    parent = os.path.dirname(STATE_PATH) or "."
    try:
        if not os.path.isdir(parent):
            os.makedirs(parent)
    except (OSError, IOError):
        pass
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        try:
            os.replace(tmp, STATE_PATH)
        except AttributeError:
            if os.path.exists(STATE_PATH):
                os.remove(STATE_PATH)
            os.rename(tmp, STATE_PATH)
    except (OSError, IOError):
        pass


def clear_state():
    try:
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
    except (OSError, IOError):
        pass


def normalize_remote_dir(remote_dir):
    remote_dir = (remote_dir or "/").strip() or "/"
    if not remote_dir.startswith("/"):
        remote_dir = "/" + remote_dir
    if remote_dir != "/":
        remote_dir = remote_dir.rstrip("/")
    return remote_dir


def connect_ftp(host, user, password, remote_dir, connect_timeout):
    ftp = FTP()
    ftp.connect(host, 21, timeout=connect_timeout)
    ftp.login(user, password)
    ftp.set_pasv(True)
    if remote_dir and remote_dir != "/":
        ftp.cwd(remote_dir)
    try:
        ftp.sock.settimeout(connect_timeout)
    except Exception:
        pass
    return ftp


def remote_size(ftp, name):
    try:
        sz = ftp.size(name)
        if sz is not None:
            return int(sz)
    except (error_perm, error_temp, OSError, IOError, socket.error, ValueError, TypeError):
        pass
    # Fallback: LIST parse
    lines = []
    try:
        ftp.retrlines("LIST " + name, lines.append)
    except (error_perm, error_temp, OSError, IOError, socket.error):
        return None
    for line in lines:
        parts = line.split()
        if len(parts) >= 5:
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def parse_args():
    p = argparse.ArgumentParser(description="FTP put with SIZE verification")
    p.add_argument("--host", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--local", required=True)
    p.add_argument("--remote", required=True)
    p.add_argument("--remote-dir", default="/")
    p.add_argument("--log", default=DEFAULT_LOG)
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="Only compare remote SIZE to local; do not upload",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Overall timeout seconds (0 = auto from file size)",
    )
    p.add_argument("--connect-timeout", type=int, default=60)
    return p.parse_args()


def main():
    args = parse_args()
    local_path = args.local
    remote_name = args.remote
    remote_dir = normalize_remote_dir(args.remote_dir)
    log_path = args.log or DEFAULT_LOG

    if not os.path.isfile(local_path):
        log_line(log_path, "HATA: local yok: %s" % local_path)
        return 1

    local_size = os.path.getsize(local_path)
    overall = args.timeout if args.timeout > 0 else timeout_sec_for_size(local_size)
    idle = min(300, max(60, args.connect_timeout))

    write_state(
        {
            "pid": os.getpid(),
            "local_path": local_path,
            "remote_name": remote_name,
            "server": args.host,
            "user": args.user,
            "remote_dir": remote_dir,
            "local_size": local_size,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeout_sec": overall,
            "verify_only": bool(args.verify_only),
        }
    )

    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(overall)

    socket.setdefaulttimeout(idle)
    ftp = None
    saw_226 = "n/a"
    transferred = 0
    last_progress = time.time()
    started = time.time()

    try:
        try:
            ftp = connect_ftp(
                args.host, args.user, args.password, remote_dir, args.connect_timeout
            )
        except OverallTimeout:
            log_line(log_path, "TIMEOUT connect host=%s" % args.host)
            return 3
        except Exception as exc:
            log_line(log_path, "CONNECT/LOGIN HATA host=%s: %s" % (args.host, exc))
            return 2

        if args.verify_only:
            rsz = remote_size(ftp, remote_name)
            if rsz is None:
                log_line(
                    log_path,
                    "VERIFY fail remote_size=None local=%s remote=%s"
                    % (local_size, remote_name),
                )
                return 1
            if int(rsz) == int(local_size):
                log_line(
                    log_path,
                    "FTP ok size_match local=%s remote=%s (226=n/a verify-only)"
                    % (local_size, rsz),
                )
                return 0
            log_line(
                log_path,
                "VERIFY mismatch local=%s remote=%s name=%s"
                % (local_size, rsz, remote_name),
            )
            return 1

        log_line(
            log_path,
            "UPLOAD start host=%s dir=%s local=%s (%s bytes) remote=%s timeout=%ss"
            % (args.host, remote_dir, local_path, local_size, remote_name, overall),
        )

        # Py2/3: mutable container for transferred bytes
        progress = {"n": 0, "t": last_progress}

        def callback(block):
            progress["n"] += len(block)
            now = time.time()
            if now - progress["t"] >= PROGRESS_INTERVAL:
                progress["t"] = now
                pct = 0
                if local_size > 0:
                    pct = int(progress["n"] * 100 / local_size)
                log_line(
                    log_path,
                    "UPLOAD progress %s/%s (%s%%) elapsed=%ss"
                    % (progress["n"], local_size, pct, int(now - started)),
                )

        try:
            with open(local_path, "rb") as fh:
                # storbinary returns None; 226 may or may not arrive depending on server
                resp = ftp.storbinary("STOR " + remote_name, fh, 8192, callback)
            transferred = progress["n"]
            if resp and "226" in str(resp):
                saw_226 = "yes"
            else:
                # Many servers complete transfer without surfacing 226 on storbinary return
                saw_226 = "no"
        except OverallTimeout:
            log_line(
                log_path,
                "TIMEOUT during STOR transferred=%s local=%s" % (progress["n"], local_size),
            )
            return 3
        except Exception as exc:
            log_line(log_path, "STOR HATA: %s transferred=%s" % (exc, progress["n"]))
            # Still try SIZE — partial may exist
            try:
                rsz = remote_size(ftp, remote_name)
            except Exception:
                rsz = None
            if rsz is not None and int(rsz) == int(local_size):
                log_line(
                    log_path,
                    "FTP ok size_match local=%s remote=%s (226=%s after-error)"
                    % (local_size, rsz, saw_226),
                )
                return 0
            return 1

        try:
            rsz = remote_size(ftp, remote_name)
        except OverallTimeout:
            log_line(log_path, "TIMEOUT during SIZE")
            return 3
        except Exception as exc:
            log_line(log_path, "SIZE HATA: %s" % exc)
            rsz = None

        if rsz is not None and int(rsz) == int(local_size):
            log_line(
                log_path,
                "FTP ok size_match local=%s remote=%s (226=%s)"
                % (local_size, rsz, saw_226),
            )
            return 0

        log_line(
            log_path,
            "FTP fail size_mismatch local=%s remote=%s transferred=%s (226=%s)"
            % (local_size, rsz, transferred or progress["n"], saw_226),
        )
        return 1

    except OverallTimeout:
        log_line(log_path, "TIMEOUT overall=%ss" % overall)
        return 3
    finally:
        if hasattr(signal, "SIGALRM"):
            try:
                signal.alarm(0)
            except Exception:
                pass
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
        clear_state()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OverallTimeout:
        eprint("TIMEOUT")
        clear_state()
        sys.exit(3)
    except KeyboardInterrupt:
        clear_state()
        sys.exit(3)
