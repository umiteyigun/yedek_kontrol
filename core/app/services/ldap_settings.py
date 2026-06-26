"""Panel LDAP ayarlarini settings.json uzerinden okur."""

from __future__ import annotations

from dataclasses import dataclass

from app.config.ldap_config import (
    LDAP_BASE_DN as DEFAULT_BASE_DN,
    LDAP_GROUP_BASE as DEFAULT_GROUP_BASE,
    LDAP_GROUPS_FULL as DEFAULT_GROUPS_FULL,
    LDAP_GROUPS_LIMITED as DEFAULT_GROUPS_LIMITED,
    LDAP_HOST as DEFAULT_HOST,
    LDAP_PORT as DEFAULT_PORT,
    LDAP_USE_SSL as DEFAULT_USE_SSL,
    LDAP_USER_DN_TEMPLATE as DEFAULT_USER_DN_TEMPLATE,
)
from app.config.models import YedekSettings


@dataclass(frozen=True)
class EffectiveLdapConfig:
    enabled: bool
    host: str
    port: int
    use_ssl: bool
    base_dn: str
    user_dn_template: str
    group_base: str
    groups_full: frozenset[str]
    groups_limited: frozenset[str]


def _split_groups(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def effective_ldap_config(settings: YedekSettings) -> EffectiveLdapConfig:
    host = (settings.ldap_host or DEFAULT_HOST).strip() or DEFAULT_HOST
    return EffectiveLdapConfig(
        enabled=bool(settings.ldap_enabled),
        host=host,
        port=int(settings.ldap_port or DEFAULT_PORT),
        use_ssl=bool(settings.ldap_use_ssl if settings.ldap_use_ssl is not None else DEFAULT_USE_SSL),
        base_dn=(settings.ldap_base_dn or DEFAULT_BASE_DN).strip() or DEFAULT_BASE_DN,
        user_dn_template=(settings.ldap_user_dn_template or DEFAULT_USER_DN_TEMPLATE).strip()
        or DEFAULT_USER_DN_TEMPLATE,
        group_base=(settings.ldap_group_base or DEFAULT_GROUP_BASE).strip() or DEFAULT_GROUP_BASE,
        groups_full=_split_groups(settings.ldap_groups_full) or DEFAULT_GROUPS_FULL,
        groups_limited=_split_groups(settings.ldap_groups_limited) or DEFAULT_GROUPS_LIMITED,
    )
