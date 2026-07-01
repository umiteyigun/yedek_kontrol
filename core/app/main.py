import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.auth import SESSION_COOKIE, SESSION_MAX_AGE, cookie_kwargs_for_request

from app.config.applier import ConfigApplier
from app.config.store import ConfigStore
from app.routes import api, backups, panel, rman, system, tablespaces, terminal
from app.services.central_proxy_auth import CENTRAL_PROXY_SECRET, MIN_CENTRAL_PROXY_SECRET_LEN
from app.services.local_roles import LocalRoleStore
from app.services.local_users import LocalUserStore
from app.services.backup_schedule import BackupScheduleService
from app.services.ftp import DynamicFTPService
from app.services.instance_discovery import InstanceDiscoveryService
from app.services.notifications import NotificationService
from app.services.retention import RetentionService
from app.services.rman_schedule import RmanScheduleService
from app.services.session_store import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
HOST_OUTPUT = Path(os.getenv("HOST_OUTPUT", "/host-output"))
GENERATED_DIR = CONFIG_DIR / "generated"
HOST_YEDEKCONFIG = Path(os.getenv("HOST_YEDEKCONFIG", "/host-config/yedekconfig.sh"))
YEDEK_DIR = Path(os.getenv("YEDEK_DIR", "/yedek/orayedek"))
BACKUP_TRIGGER = HOST_OUTPUT / "backup.trigger"
LOCAL_FTP_ENABLED = os.getenv("LOCAL_FTP_ENABLED", "").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ConfigStore(CONFIG_DIR)
    applier = ConfigApplier(HOST_OUTPUT, GENERATED_DIR)
    ftp = DynamicFTPService(store)
    retention = RetentionService(store, YEDEK_DIR)
    notifications = NotificationService(CONFIG_DIR)
    discovery = InstanceDiscoveryService(store)
    backup_schedule = BackupScheduleService(store, BACKUP_TRIGGER)
    rman_schedule = RmanScheduleService(store, BACKUP_TRIGGER)
    session_store = SessionStore(CONFIG_DIR / "sessions.json")
    local_user_store = LocalUserStore(CONFIG_DIR / "local_users.json")
    local_role_store = LocalRoleStore(CONFIG_DIR / "local_roles.json")

    settings_file = CONFIG_DIR / "settings.json"
    if not settings_file.exists() and HOST_YEDEKCONFIG.exists():
        store.import_from_yedekconfig(HOST_YEDEKCONFIG)

    settings = store.load()
    discovery.run()
    settings = store.get()
    applier.apply(settings)
    if LOCAL_FTP_ENABLED:
        ftp.start(settings)
        store.subscribe(ftp.reload)
        logger.info("Yerel FTP sunucusu aktif")
    else:
        logger.info("Yerel FTP kapali — yedekler uzak FTP'ye instance ayarindan gonderilir")
    if not CENTRAL_PROXY_SECRET or len(CENTRAL_PROXY_SECRET) < MIN_CENTRAL_PROXY_SECRET_LEN:
        logger.warning(
            "CENTRAL_PROXY_SECRET eksik veya kisa — merkez hub proxy oturumu reddedilir"
        )
    retention.start()
    backup_schedule.start(settings)
    rman_schedule.start(settings)
    discovery.start()

    store.subscribe(applier.apply)
    store.subscribe(backup_schedule.reload)
    store.subscribe(rman_schedule.reload)

    app.state.store = store
    app.state.applier = applier
    app.state.ftp = ftp
    app.state.yedek_dir = YEDEK_DIR
    app.state.backup_trigger = BACKUP_TRIGGER
    app.state.notifications = notifications
    app.state.discovery = discovery
    app.state.backup_schedule = backup_schedule
    app.state.rman_schedule = rman_schedule
    app.state.session_store = session_store
    app.state.local_user_store = local_user_store
    app.state.local_role_store = local_role_store

    logger.info(
        "yedek-core hazir | config v%s | %s instance",
        store.version,
        len(settings.instances),
    )
    yield
    discovery.stop()
    backup_schedule.stop()
    rman_schedule.stop()
    retention.stop()
    if LOCAL_FTP_ENABLED:
        ftp.stop()


app = FastAPI(title="Yedek Core", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)

PANEL_SERVER_HEADER = os.getenv("PANEL_SERVER_HEADER", "YedekPanel")


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    if "server" in response.headers:
        del response.headers["server"]
    if PANEL_SERVER_HEADER:
        response.headers["Server"] = PANEL_SERVER_HEADER
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.middleware("http")
async def central_proxy_session_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    cookie = getattr(request.state, "central_session_cookie", None)
    if cookie:
        response.set_cookie(
            SESSION_COOKIE,
            cookie,
            **cookie_kwargs_for_request(request, max_age=SESSION_MAX_AGE),
        )
    return response


app.include_router(panel.router)
app.include_router(backups.router)
app.include_router(rman.router)
app.include_router(system.router)
app.include_router(tablespaces.router)
app.include_router(terminal.router)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
