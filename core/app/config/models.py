import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config.ldap_config import apply_ldap_defaults_to_dict


INSTANCE_FIELDS = (
    "hastane",
    "il",
    "password",
    "schemas",
    "kurumkodu",
    "directory",
    "directorydizini",
    "oracle_sid",
    "yedek_kodu",
    "guid_key",
    "localftpip",
    "localftpuser",
    "localftppass",
    "localftpdir",
    "localftpip2",
    "localftpuser2",
    "localftppass2",
    "localftpdir2",
    "ftp2_upload_enabled",
    "retention_days",
    "backup_protect_mode",
    "backup_protect_pass",
    "backup_split_enabled",
    "backup_split_size_mb",
    "ftp_upload_enabled",
)


_FTP_PLACEHOLDER_IPS = frozenset({"", "127.0.0.1", "ftp_ip"})


def slugify(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "instance"


_TURKISH_TO_ASCII = str.maketrans(
    {
        "ı": "I",
        "İ": "I",
        "i": "I",
        "ğ": "G",
        "Ğ": "G",
        "ü": "U",
        "Ü": "U",
        "ş": "S",
        "Ş": "S",
        "ö": "O",
        "Ö": "O",
        "ç": "C",
        "Ç": "C",
    }
)


def normalize_upper_ascii(value: str) -> str:
    """Gorunen ad / il: Turkce harfleri ASCII'ye cevir, buyuk harf, yalnizca A-Z ve bosluk."""
    text = (value or "").strip().translate(_TURKISH_TO_ASCII).upper()
    text = re.sub(r"[^A-Z ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


WEEKDAY_LABELS = (
    "Pazartesi",
    "Sali",
    "Carsamba",
    "Persembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
)


class BackupScheduleRule(BaseModel):
    id: str
    enabled: bool = True
    backup_type: Literal["GUNLUK", "HAFTALIK"] = "GUNLUK"
    time: str = "02:00"
    day_of_week: int | None = None
    label: str = ""
    ftp_target: Literal["primary", "secondary", "none"] = "primary"

    @field_validator("id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        clean = slugify(value)
        if not clean:
            raise ValueError("Zamanlama id gecersiz")
        return clean

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        raw = (value or "").strip()
        if len(raw) == 5 and raw[2] == ":":
            hour, minute = raw.split(":", 1)
        elif len(raw) == 4 and raw.isdigit():
            hour, minute = raw[:2], raw[2:]
        else:
            raise ValueError("Saat HH:MM formatinda olmali")
        if not (hour.isdigit() and minute.isdigit()):
            raise ValueError("Saat HH:MM formatinda olmali")
        h, m = int(hour), int(minute)
        if h > 23 or m > 59:
            raise ValueError("Saat araligi gecersiz")
        return f"{h:02d}:{m:02d}"

    @field_validator("day_of_week")
    @classmethod
    def validate_day(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0 or value > 6:
            raise ValueError("Haftanin gunu 0-6 arasi olmali")
        return value

    @model_validator(mode="after")
    def normalize_weekly(self) -> "BackupScheduleRule":
        if self.backup_type == "HAFTALIK" and self.day_of_week is None:
            self.day_of_week = 6
        if self.backup_type == "GUNLUK":
            self.day_of_week = None
        return self

    def summary(self) -> str:
        if self.backup_type == "GUNLUK":
            return f"Her gun {self.time}"
        day = WEEKDAY_LABELS[self.day_of_week or 6]
        return f"Her {day} {self.time}"

    def backup_type_label(self) -> str:
        return "Gunluk" if self.backup_type == "GUNLUK" else "Haftalik"

    def ftp_target_label(self) -> str:
        labels = {
            "primary": "FTP-1 (birincil)",
            "secondary": "FTP-2 (ikincil)",
            "none": "FTP yok",
        }
        return labels.get(self.ftp_target, "FTP-1 (birincil)")


class RmanScheduleRule(BaseModel):
    id: str
    enabled: bool = True
    backup_type: Literal["RMAN_FULL", "RMAN_INCR"] = "RMAN_FULL"
    time: str = "03:00"
    day_of_week: int | None = None
    label: str = ""

    @field_validator("id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        clean = slugify(value)
        if not clean:
            raise ValueError("RMAN zamanlama id gecersiz")
        return clean

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        raw = (value or "").strip()
        if len(raw) == 5 and raw[2] == ":":
            hour, minute = raw.split(":", 1)
        elif len(raw) == 4 and raw.isdigit():
            hour, minute = raw[:2], raw[2:]
        else:
            raise ValueError("Saat HH:MM formatinda olmali")
        if not (hour.isdigit() and minute.isdigit()):
            raise ValueError("Saat HH:MM formatinda olmali")
        h, m = int(hour), int(minute)
        if h > 23 or m > 59:
            raise ValueError("Saat araligi gecersiz")
        return f"{h:02d}:{m:02d}"

    @field_validator("day_of_week")
    @classmethod
    def validate_day(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0 or value > 6:
            raise ValueError("Haftanin gunu 0-6 arasi olmali")
        return value

    @model_validator(mode="after")
    def normalize_schedule(self) -> "RmanScheduleRule":
        if self.backup_type == "RMAN_FULL" and self.day_of_week is None:
            self.day_of_week = 6
        if self.backup_type == "RMAN_INCR":
            self.day_of_week = None
        return self

    def summary(self) -> str:
        if self.backup_type == "RMAN_INCR":
            return f"Her gun {self.time} (fark)"
        day = WEEKDAY_LABELS[self.day_of_week or 6]
        return f"Her {day} {self.time} (full)"

    def backup_type_label(self) -> str:
        return "Haftalik Full" if self.backup_type == "RMAN_FULL" else "Gunluk Fark"


class InstanceSettings(BaseModel):
    id: str
    enabled: bool = True
    label: str = ""
    hastane: str = ""
    il: str = ""
    password: str = ""
    schemas: str = "SYSTEM"
    kurumkodu: str = ""
    directory: str = "TRTEK"
    directorydizini: str = ""
    oracle_sid: str = "orcl"
    yedek_kodu: str = "Hbys"
    guid_key: str = ""
    localftpip: str = ""
    localftpuser: str = ""
    localftppass: str = ""
    localftpdir: str = "/"
    ftp_upload_enabled: bool = False
    localftpip2: str = ""
    localftpuser2: str = ""
    localftppass2: str = ""
    localftpdir2: str = "/"
    ftp2_upload_enabled: bool = False
    retention_days: int = Field(default=0, ge=0, le=365)
    backup_protect_mode: Literal["gzip", "oracle", "zip"] = "gzip"
    backup_protect_pass: str = ""
    backup_split_enabled: bool = False
    backup_split_size_mb: int = Field(default=2048, ge=512, le=8192)
    schedules: list[BackupScheduleRule] = Field(default_factory=list)
    rman_enabled: bool = False
    rman_dest: str = "/yedek/rman"
    rman_archivelog_backup: bool = False
    rman_retention_days: int = Field(default=14, ge=1, le=365)
    rman_channels: int = Field(default=2, ge=1, le=4)
    rman_compression: bool = True
    rman_schedules: list[RmanScheduleRule] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_ftp_upload_enabled(cls, data: Any) -> Any:
        if isinstance(data, dict) and "ftp_upload_enabled" not in data:
            ip = str(data.get("localftpip", "")).strip()
            data["ftp_upload_enabled"] = ip not in _FTP_PLACEHOLDER_IPS
        return data

    @field_validator("backup_protect_mode")
    @classmethod
    def validate_backup_protect_mode(cls, value: str) -> str:
        clean = (value or "gzip").strip().lower()
        if clean not in {"gzip", "oracle", "zip"}:
            raise ValueError("backup_protect_mode gzip, oracle veya zip olmali")
        return clean

    @field_validator("localftpdir", "localftpdir2")
    @classmethod
    def validate_localftpdir(cls, value: str) -> str:
        raw = (value or "/").strip().replace("\\", "/")
        if not raw or raw == "/":
            return "/"
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return raw.rstrip("/")

    @field_validator("rman_dest")
    @classmethod
    def validate_rman_dest(cls, value: str) -> str:
        raw = (value or "/yedek/rman").strip().replace("\\", "/")
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return raw.rstrip("/") or "/yedek/rman"

    @field_validator("label", "il")
    @classmethod
    def validate_upper_ascii_label_fields(cls, value: str) -> str:
        return normalize_upper_ascii(value)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        clean = slugify(value)
        if not clean:
            raise ValueError("Instance id gecersiz")
        return clean

    def display_name(self) -> str:
        return self.label or self.hastane or self.oracle_sid or self.id

    def backup_file_prefix(self) -> str:
        """Yedek dosya adi on eki: label > hastane > id (buyuk ASCII, bosluksuz)."""
        raw = self.label or self.hastane or self.id
        normalized = normalize_upper_ascii(raw).replace(" ", "")
        if normalized:
            return normalized
        return self.id.upper().replace("-", "")

    def effective_directorydizini(self, yedek_dir: str) -> str:
        """Tum kurumlar tek dizinde: /yedek/orayedek/"""
        return f"{yedek_dir.rstrip('/')}/"

    def effective_rman_dest(self) -> str:
        """Instance bazli RMAN kok dizini: /yedek/rman/<id>/"""
        base = self.rman_dest.rstrip("/") or "/yedek/rman"
        return f"{base}/{self.id}"

    def rman_type_folder(self, backup_type: str) -> str:
        if backup_type == "RMAN_INCR":
            return "fark"
        if backup_type == "RMAN_FULL_MANUAL":
            return "full/manuel"
        return "full"

    def backup_prefixes(self) -> list[str]:
        prefixes: list[str] = []
        file_prefix = self.backup_file_prefix()
        if file_prefix:
            prefixes.append(file_prefix)
            prefixes.append(file_prefix.lower())
        if self.id:
            prefixes.append(self.id.lower())
            prefixes.append(self.id.upper().replace("-", ""))
        if self.label:
            clean = normalize_upper_ascii(self.label).replace(" ", "")
            if clean:
                prefixes.append(clean)
                prefixes.append(clean.lower())
        if self.hastane:
            prefixes.append(self.hastane.upper().replace(" ", ""))
            prefixes.append(self.hastane.lower())
        return list(dict.fromkeys(prefixes))

    def backup_base_name(self, filename: str) -> str:
        name = filename
        # Yeni split: dosya.dmp.gz.part_001
        if ".part_" in name:
            name = re.sub(r"\.part_\d+$", "", name)
        # Eski yedek.sh split: dosya.dmp.gz-part-aa
        if "-part-" in name:
            name = re.sub(r"-part-[A-Za-z0-9]+$", "", name)
        for suffix in (".dmp.gz", ".dmp", ".zip"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name.removesuffix(".log")

    def matches_backup_file(self, filename: str) -> bool:
        if not (
            filename.endswith(".dmp.gz")
            or filename.endswith(".dmp")
            or filename.endswith(".zip")
            or ".part_" in filename
            or "-part-" in filename
        ):
            return False
        base = self.backup_base_name(filename)
        for prefix in self.backup_prefixes():
            if not prefix:
                continue
            if base.lower() == prefix.lower():
                return True
            if base.upper().startswith(prefix.upper()):
                return True
        return False

    def backup_log_name(self, archive_name: str) -> str:
        return self.backup_base_name(archive_name) + ".log"

    def effective_retention(self, global_days: int) -> int:
        return self.retention_days if self.retention_days > 0 else global_days

    def effective_ftp(self, settings: "YedekSettings") -> tuple[str, str, str]:
        return (
            self.localftpip or settings.localftpip,
            self.localftpuser or settings.localftpuser,
            self.localftppass or settings.localftppass,
        )

    def effective_ftp2(self, settings: "YedekSettings") -> tuple[str, str, str]:
        return (
            self.localftpip2 or settings.localftpip2,
            self.localftpuser2 or settings.localftpuser2,
            self.localftppass2 or settings.localftppass2,
        )

    def ftp_credentials_for_target(
        self,
        settings: "YedekSettings",
        target: str,
    ) -> tuple[str, str, str, str]:
        if target == "secondary":
            host, user, password = self.effective_ftp2(settings)
            return host, user, password, self.localftpdir2 or "/"
        host, user, password = self.effective_ftp(settings)
        return host, user, password, self.localftpdir or "/"

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["password"] = "***"
        data["localftppass"] = "***"
        data["localftppass2"] = "***"
        data["backup_protect_pass"] = "***"
        return data


class YedekSettings(BaseModel):
    oracle_ver: str = "11.2.0.4"
    hostname: str = "data"
    yedek_dir: str = "/yedek/orayedek"
    remote_api_url: str = ""
    mail_notify: bool = True
    retention_days: int = Field(default=2, ge=1, le=365)
    panel_log_retention_days: int = Field(default=2, ge=1, le=90)
    localftpip: str = "127.0.0.1"
    localftpuser: str = "ftp"
    localftppass: str = "ftp"
    localftpip2: str = ""
    localftpuser2: str = ""
    localftppass2: str = ""
    pasv_address: str = "127.0.0.1"
    ftp_port: int = 21
    pasv_min_port: int = 21100
    pasv_max_port: int = 21110
    core_port: int = 8090
    backup_disk_max_pct: int = Field(default=90, ge=80, le=99)
    backup_disk_min_free_gb: float = Field(default=5.0, ge=0.5)
    backup_disk_reserve_gb: float = Field(default=2.0, ge=0)
    backup_size_margin_pct: int = Field(default=25, ge=0, le=200)
    server_timezone: str = "Europe/Istanbul"
    auth_mode: Literal["ldap", "local", "ldap_and_local"] = "ldap_and_local"
    ldap_enabled: bool = True
    ldap_host: str = "ad.trtekyazilim.com"
    ldap_port: int = Field(default=389, ge=1, le=65535)
    ldap_use_ssl: bool = False
    ldap_base_dn: str = "dc=trtekyazilim,dc=com"
    ldap_user_dn_template: str = "uid={username},cn=users,cn=accounts,dc=trtekyazilim,dc=com"
    ldap_group_base: str = "cn=groups,cn=accounts,dc=trtekyazilim,dc=com"
    ldap_groups_full: str = "admins,admin,ipaadmins"
    ldap_groups_limited: str = "yedek-data"
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_search_filter: str = "(uid={username})"
    instances: list[InstanceSettings] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def apply_ldap_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return apply_ldap_defaults_to_dict(data)
        return data

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_flat_config(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("instances"):
            return data

        instance_data: dict[str, Any] = {}
        for key in INSTANCE_FIELDS:
            if key in data:
                instance_data[key] = data.pop(key)

        base_id = slugify(instance_data.get("hastane") or instance_data.get("oracle_sid") or "varsayilan")
        instance_data.setdefault("id", base_id)
        instance_data.setdefault("label", instance_data.get("hastane") or instance_data.get("oracle_sid") or "Varsayilan")
        instance_data.setdefault("enabled", True)
        if not instance_data.get("guid_key"):
            instance_data["guid_key"] = str(uuid.uuid4())

        yedek_dir = data.get("yedek_dir", "/yedek/orayedek")
        instance_data.setdefault("directorydizini", f"{str(yedek_dir).rstrip('/')}/")

        data["instances"] = [instance_data]
        return data

    def enabled_instances(self) -> list[InstanceSettings]:
        return [item for item in self.instances if item.enabled]

    def get_instance(self, instance_id: str | None) -> InstanceSettings | None:
        if not instance_id:
            return self.instances[0] if self.instances else None
        for item in self.instances:
            if item.id == instance_id:
                return item
        return None

    def first_instance(self) -> InstanceSettings | None:
        return self.instances[0] if self.instances else None

    @property
    def hastane(self) -> str:
        return self.first_instance().hastane if self.first_instance() else ""

    @property
    def il(self) -> str:
        return self.first_instance().il if self.first_instance() else ""

    @property
    def password(self) -> str:
        return self.first_instance().password if self.first_instance() else ""

    @property
    def schemas(self) -> str:
        return self.first_instance().schemas if self.first_instance() else ""

    @property
    def kurumkodu(self) -> str:
        return self.first_instance().kurumkodu if self.first_instance() else ""

    @property
    def directory(self) -> str:
        return self.first_instance().directory if self.first_instance() else ""

    @property
    def directorydizini(self) -> str:
        inst = self.first_instance()
        return inst.effective_directorydizini(self.yedek_dir) if inst else ""

    @property
    def oracle_sid(self) -> str:
        return self.first_instance().oracle_sid if self.first_instance() else ""

    @property
    def yedek_kodu(self) -> str:
        return self.first_instance().yedek_kodu if self.first_instance() else ""

    @property
    def guid_key(self) -> str:
        return self.first_instance().guid_key if self.first_instance() else ""

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["instances"] = [item.public_dict() for item in self.instances]
        data["localftppass"] = "***"
        data["ldap_bind_password"] = "***"
        return data
