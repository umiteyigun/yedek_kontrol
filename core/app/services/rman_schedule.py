import logging
from functools import partial
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config.models import YedekSettings
from app.config.store import ConfigStore
from app.services.backups import queue_rman_backup
from app.services.disk_guard import check_rman_disk_space, record_backup_skip

from app.services.backup_schedule import DEFAULT_SCHEDULE_TIMEZONE, schedule_timezone

logger = logging.getLogger(__name__)


class RmanScheduleService:
    """RMAN haftalik full ve gunluk fark zamanlamasi."""

    def __init__(self, store: ConfigStore, trigger_path: Path) -> None:
        self._store = store
        self._trigger_path = trigger_path
        self._scheduler = BackgroundScheduler(timezone=DEFAULT_SCHEDULE_TIMEZONE)

    def start(self, settings: YedekSettings) -> None:
        self.reload(settings)
        if not self._scheduler.running:
            self._scheduler.start()
        logger.info("RMAN zamanlayici baslatildi")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def reload(self, settings: YedekSettings) -> None:
        if self._scheduler.running:
            self._scheduler.remove_all_jobs()

        job_count = 0
        tz = schedule_timezone(settings)
        for inst in settings.instances:
            if not inst.enabled or not inst.rman_enabled:
                continue
            for rule in inst.rman_schedules:
                if not rule.enabled:
                    continue
                hour, minute = (int(part) for part in rule.time.split(":", 1))
                if rule.backup_type == "RMAN_INCR":
                    trigger = CronTrigger(
                        hour=hour,
                        minute=minute,
                        timezone=tz,
                    )
                else:
                    trigger = CronTrigger(
                        day_of_week=rule.day_of_week or 6,
                        hour=hour,
                        minute=minute,
                        timezone=tz,
                    )
                job_id = f"rman:{inst.id}:{rule.id}"
                self._scheduler.add_job(
                    partial(self._queue, inst.id, rule.backup_type),
                    trigger=trigger,
                    id=job_id,
                    replace_existing=True,
                )
                job_count += 1

        logger.info("RMAN zamanlama guncellendi: %s aktif kural", job_count)

    def _queue(self, instance_id: str, backup_type: str) -> None:
        try:
            settings = self._store.get()
            instance = settings.get_instance(instance_id)
            if not instance or not instance.rman_enabled:
                logger.warning("RMAN zamanlama atlandi: instance yok veya kapali (%s)", instance_id)
                return

            disk_check = check_rman_disk_space(settings, instance, backup_type)
            if not disk_check.ok:
                yedek_dir = Path(settings.yedek_dir)
                record_backup_skip(
                    yedek_dir,
                    instance_id,
                    backup_type,
                    disk_check,
                    scheduled=True,
                )
                return

            queue_rman_backup(self._trigger_path, backup_type, instance_id)
            logger.info("RMAN zamanlanmis yedek kuyruga alindi: %s %s", backup_type, instance_id)
        except Exception:
            logger.exception("RMAN zamanlanmis yedek tetiklenemedi: %s %s", backup_type, instance_id)
