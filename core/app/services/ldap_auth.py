import logging
import re
import socket
import time

from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPException

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED
from app.config.models import YedekSettings
from app.services.ldap_settings import EffectiveLdapConfig, effective_ldap_config

logger = logging.getLogger(__name__)

_UNREACHABLE_CACHE: dict[str, float] = {}
_UNREACHABLE_CACHE_TTL_SEC = 60


def _ldap_endpoint(host: str, port: int, use_ssl: bool) -> str:
    scheme = "ldaps" if use_ssl else "ldap"
    return f"{scheme}://{host}:{port}"


def _ldap_unreachable_cached(host: str, port: int, use_ssl: bool) -> bool:
    key = _ldap_endpoint(host, port, use_ssl)
    until = _UNREACHABLE_CACHE.get(key)
    if until is None:
        return False
    if time.time() >= until:
        _UNREACHABLE_CACHE.pop(key, None)
        return False
    return True


def _mark_ldap_unreachable(host: str, port: int, use_ssl: bool) -> None:
    key = _ldap_endpoint(host, port, use_ssl)
    _UNREACHABLE_CACHE[key] = time.time() + _UNREACHABLE_CACHE_TTL_SEC


def _probe_ldap_reachable(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _group_name_from_dn(dn: str) -> str:
    match = re.search(r"cn=([^,]+)", dn, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _resolve_role(group_names: set[str], cfg: EffectiveLdapConfig) -> str | None:
    if group_names & set(cfg.groups_full):
        return ROLE_FULL
    if group_names & set(cfg.groups_limited):
        return ROLE_LIMITED
    return None


def _fetch_groups(conn: Connection, user_dn: str, cfg: EffectiveLdapConfig) -> set[str]:
    names: set[str] = set()
    conn.search(user_dn, "(objectClass=*)", attributes=["memberOf"])
    if conn.entries:
        attrs = conn.entries[0].entry_attributes_as_dict
        for dn in attrs.get("memberOf", []):
            name = _group_name_from_dn(str(dn))
            if name:
                names.add(name)
    if names:
        return names

    conn.search(
        cfg.group_base,
        f"(&(objectClass=groupOfNames)(member={user_dn}))",
        attributes=["cn"],
    )
    for entry in conn.entries:
        if "cn" in entry:
            names.add(str(entry.cn.value).lower())
    return names


def ldap_login(username: str, password: str, settings: YedekSettings | None = None) -> tuple[bool, str | None]:
    """LDAP/FreeIPA giris + grup yetkisi. Donus: (basarili, role)."""
    ok, role, _detail = ldap_login_detail(username, password, settings)
    return ok, role


def ldap_login_detail(
    username: str, password: str, settings: YedekSettings | None = None
) -> tuple[bool, str | None, str]:
    """Donus: (ok, role, detail). detail hata/ag bilgisini tasir."""
    if not username or not password:
        return False, None, "Kullanici veya sifre bos"

    cfg = effective_ldap_config(settings) if settings else effective_ldap_config(YedekSettings())
    if not cfg.enabled:
        return False, None, "LDAP kapali"

    if _ldap_unreachable_cached(cfg.host, cfg.port, cfg.use_ssl):
        return False, None, f"LDAP sunucusuna ulasilamiyor ({cfg.host}:{cfg.port})"
    if not _probe_ldap_reachable(cfg.host, cfg.port):
        _mark_ldap_unreachable(cfg.host, cfg.port, cfg.use_ssl)
        logger.info("LDAP sunucusuna TCP baglantisi yok (%s:%s)", cfg.host, cfg.port)
        return False, None, f"LDAP sunucusuna ulasilamiyor ({cfg.host}:{cfg.port})"

    user_dn = cfg.user_dn_template.format(username=username)
    try:
        server = Server(
            cfg.host,
            port=cfg.port,
            use_ssl=cfg.use_ssl,
            get_info=ALL,
            connect_timeout=8,
        )
        with Connection(server, user=user_dn, password=password, auto_bind=True) as conn:
            groups = _fetch_groups(conn, user_dn, cfg)
            role = _resolve_role(groups, cfg)
            if role is None:
                msg = f"Uygun LDAP grubu yok: {sorted(groups) or '-'}"
                logger.info("LDAP giris reddedildi (%s): %s", username, msg)
                return False, None, msg
            logger.info("LDAP giris OK (%s) role=%s gruplar=%s", username, role, groups)
            return True, role, "ok"
    except LDAPException as exc:
        text = str(exc)
        logger.info("LDAP giris basarisiz (%s): %s", username, text)
        low = text.lower()
        if "timed out" in low or "timeout" in low or "errno 110" in low:
            _mark_ldap_unreachable(cfg.host, cfg.port, cfg.use_ssl)
            return False, None, f"LDAP sunucusuna ulasilamiyor ({cfg.host}:{cfg.port})"
        if "invalidcredentials" in low or "invalid credentials" in low:
            return False, None, "LDAP kullanici/sifre hatali"
        return False, None, text
    except Exception as exc:  # noqa: BLE001
        logger.exception("LDAP beklenmeyen hata (%s)", username)
        return False, None, str(exc)


def test_ldap_connection(settings: YedekSettings) -> tuple[bool, str]:
    cfg = effective_ldap_config(settings)
    if not cfg.host:
        return False, "LDAP sunucu adresi bos"
    if not _probe_ldap_reachable(cfg.host, cfg.port):
        _mark_ldap_unreachable(cfg.host, cfg.port, cfg.use_ssl)
        return False, f"LDAP sunucusuna ulasilamiyor ({cfg.host}:{cfg.port})"
    try:
        server = Server(cfg.host, port=cfg.port, use_ssl=cfg.use_ssl, get_info=ALL, connect_timeout=8)
        conn = Connection(server, auto_bind=True)
        conn.unbind()
        return True, f"Baglanti OK: {cfg.host}:{cfg.port}"
    except LDAPException as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
