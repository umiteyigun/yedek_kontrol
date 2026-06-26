import logging
import secrets
import threading
from pathlib import Path

from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from app.config.models import YedekSettings
from app.config.store import ConfigStore

logger = logging.getLogger(__name__)


class LiveAuthorizer(DummyAuthorizer):
    """Her login'de canli config okur; restart gerekmez."""

    def __init__(self, store: ConfigStore) -> None:
        super().__init__()
        self._store = store

    def validate_authentication(self, username: str, password: str, msg: str) -> None:  # noqa: ARG002
        settings = self._store.get()
        if username == settings.localftpuser and secrets.compare_digest(password, settings.localftppass):
            self._ensure_user(settings)
            return
        raise AuthenticationFailed("Gecersiz FTP bilgisi.")

    def _ensure_user(self, settings: YedekSettings) -> None:
        home = str(Path(settings.yedek_dir).resolve())
        if settings.localftpuser not in self.user_table:
            self.add_user(settings.localftpuser, settings.localftppass, home, perm="elradfmw")
            return
        entry = self.user_table[settings.localftpuser]
        entry.password = settings.localftppass
        entry.homedir = home


class DynamicFTPService:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._server: FTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active_port: int | None = None

    def start(self, settings: YedekSettings) -> None:
        with self._lock:
            self._boot(settings)

    def reload(self, settings: YedekSettings) -> None:
        with self._lock:
            if self._server is None or self._active_port != settings.ftp_port:
                logger.info("FTP yeniden baslatiliyor port=%s", settings.ftp_port)
                self._boot(settings)
                return
            authorizer = self._server.handler.authorizer
            if isinstance(authorizer, LiveAuthorizer):
                authorizer._ensure_user(settings)
            handler = self._server.handler
            handler.passive_ports = range(settings.pasv_min_port, settings.pasv_max_port + 1)
            handler.masquerade_address = settings.pasv_address
            logger.info("FTP canli guncellendi user=%s dir=%s", settings.localftpuser, settings.yedek_dir)

    def stop(self) -> None:
        with self._lock:
            if self._server:
                self._server.close_all()
                self._server = None
            self._thread = None

    def _boot(self, settings: YedekSettings) -> None:
        if self._server:
            self._server.close_all()

        authorizer = LiveAuthorizer(self._store)
        authorizer._ensure_user(settings)

        handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "Yedek FTP"
        handler.passive_ports = range(settings.pasv_min_port, settings.pasv_max_port + 1)
        handler.masquerade_address = settings.pasv_address

        address = ("0.0.0.0", settings.ftp_port)
        self._server = FTPServer(address, handler)
        self._server.max_cons = 32
        self._server.max_cons_per_ip = 8

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="yedek-ftp")
        self._thread.start()
        self._active_port = settings.ftp_port
        logger.info("FTP baslatildi port=%s root=%s", settings.ftp_port, settings.yedek_dir)
