import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config.store import ConfigStore
from app.services.oracle_discovery import sync_instances_from_oratab

logger = logging.getLogger(__name__)


class InstanceDiscoveryService:
    """Kurulum sonrasi ve periyodik olarak /etc/oratab uzerinden instance kesfi."""

    def __init__(self, store: ConfigStore, interval_minutes: int = 5) -> None:
        self._store = store
        self._interval_minutes = interval_minutes
        self._scheduler = BackgroundScheduler(timezone="Europe/Istanbul")

    def run(self) -> list[str]:
        current = self._store.get().model_dump()
        updated, added = sync_instances_from_oratab(current)
        if added:
            self._store.replace(updated)
            logger.info("Instance kesfi tamamlandi: %s", ", ".join(added))
        return added

    def start(self) -> None:
        self._scheduler.add_job(
            self.run,
            "interval",
            minutes=self._interval_minutes,
            id="oratab-discovery",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("oratab instance kesfi zamanlayicisi baslatildi (her %s dk)", self._interval_minutes)

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
