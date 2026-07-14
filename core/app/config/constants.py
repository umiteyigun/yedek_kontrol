"""Sabit Oracle / panel kurallari."""

ORACLE_DIRECTORY_NAME = "TRTEK"

# Oracle'dan otomatik gelir, panelden degistirilemez
LOCKED_GLOBAL_FIELDS = ("oracle_ver", "hostname", "yedek_dir")

# Instance bazli kilitli alanlar (directorydizini panelden duzenlenebilir — coklu instance)
LOCKED_INSTANCE_FIELDS = ("oracle_sid", "directory")
