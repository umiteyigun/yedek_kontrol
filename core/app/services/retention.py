import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.config.models import YedekSettings
from app.config.store import ConfigStore

logger = logging.getLogger(__name__)

PANEL_LOG_PREFIX = "panel-backup-"
WATCHER_LOG_NAME = "backup-watcher.log"
WATCHER_LOG_MAX_BYTES = 100_000


class RetentionService:
    def __init__(self, store: ConfigStore, yedek_dir: Path) -> None:
        self._store = store
        self._yedek_dir = yedek_dir
        self._scheduler = BackgroundScheduler(timezone="Europe/Istanbul")

    def start(self) -> None:
        self._scheduler.add_job(self.run, "cron", hour=3, minute=0, id="retention")
        self._scheduler.start()
        logger.info("Retention zamanlayici baslatildi")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    @staticmethod
    def _age_days(path: Path, now_ts: float) -> float:
        return (now_ts - path.stat().st_mtime) / 86400

    @staticmethod
    def _retention_for_backup_file(settings: YedekSettings, filename: str) -> int:
        for instance in settings.instances:
            if instance.matches_backup_file(filename):
                return instance.effective_retention(settings.retention_days)
        return settings.retention_days

    @classmethod
    def _retention_for_oracle_log(cls, settings: YedekSettings, log_name: str) -> int:
        base = log_name.removesuffix(".log")
        for suffix in (".dmp.gz", ".dmp", ".zip"):
            for instance in settings.instances:
                if instance.matches_backup_file(f"{base}{suffix}"):
                    return instance.effective_retention(settings.retention_days)
        return settings.retention_days

    def run(self) -> None:
        settings = self._store.get()
        deleted = 0
        if not self._yedek_dir.exists():
            return

        now_ts = datetime.now().timestamp()
        for path in list(self._yedek_dir.iterdir()):
            if not path.is_file():
                continue

            name = path.name
            age_days = self._age_days(path, now_ts)

            if name.startswith(PANEL_LOG_PREFIX) and name.endswith(".log"):
                if age_days > settings.panel_log_retention_days:
                    path.unlink(missing_ok=True)
                    deleted += 1
                continue

            if name == WATCHER_LOG_NAME:
                if age_days > settings.panel_log_retention_days:
                    path.unlink(missing_ok=True)
                    deleted += 1
                elif path.stat().st_size > WATCHER_LOG_MAX_BYTES:
                    path.write_text("", encoding="utf-8")
                continue

            if name.endswith(".log"):
                keep_days = self._retention_for_oracle_log(settings, name)
                if age_days > keep_days:
                    path.unlink(missing_ok=True)
                    deleted += 1
                continue

            if (
                name.endswith(".dmp.gz")
                or name.endswith(".dmp")
                or name.endswith(".zip")
                or ".part_" in name
            ):
                keep_days = self._retention_for_backup_file(settings, name)
                if age_days > keep_days:
                    path.unlink(missing_ok=True)
                    deleted += 1

        logger.info(
            "Retention tamamlandi: %s dosya silindi [%s]",
            deleted,
            datetime.now(timezone.utc).isoformat(),
        )
