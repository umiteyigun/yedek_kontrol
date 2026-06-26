"""TRTEK FreeIPA / LDAP varsayilanlari — ad.trtekyazilim.com"""

from __future__ import annotations

from typing import Any

LDAP_HOST = "ad.trtekyazilim.com"
LDAP_PORT = 389
LDAP_USE_SSL = False
LDAP_BASE_DN = "dc=trtekyazilim,dc=com"
LDAP_USER_DN_TEMPLATE = "uid={username},cn=users,cn=accounts,dc=trtekyazilim,dc=com"
LDAP_GROUP_BASE = "cn=groups,cn=accounts,dc=trtekyazilim,dc=com"
LDAP_SEARCH_FILTER = "(uid={username})"
LDAP_GROUPS_FULL_STR = "admins,admin,ipaadmins"
LDAP_GROUPS_LIMITED_STR = "yedek-data"
LDAP_AUTH_MODE_DEFAULT = "ldap_and_local"

# Tam yetki: ayarlar + tum islemler
LDAP_GROUPS_FULL = frozenset({"admins", "admin", "ipaadmins"})

# Sinirli yetki: yedek goruntuleme/baslatma, ayarlar YOK
LDAP_GROUPS_LIMITED = frozenset({"yedek-data"})

ROLE_FULL = "full"
ROLE_LIMITED = "limited"

LDAP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "auth_mode": LDAP_AUTH_MODE_DEFAULT,
    "ldap_enabled": True,
    "ldap_host": LDAP_HOST,
    "ldap_port": LDAP_PORT,
    "ldap_use_ssl": LDAP_USE_SSL,
    "ldap_base_dn": LDAP_BASE_DN,
    "ldap_user_dn_template": LDAP_USER_DN_TEMPLATE,
    "ldap_group_base": LDAP_GROUP_BASE,
    "ldap_groups_full": LDAP_GROUPS_FULL_STR,
    "ldap_groups_limited": LDAP_GROUPS_LIMITED_STR,
    "ldap_search_filter": LDAP_SEARCH_FILTER,
}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def ldap_config_is_uninitialized(data: dict[str, Any]) -> bool:
    """Eski/bos settings.json — TRTEK sablonu uygulanmali."""
    return _is_blank(data.get("ldap_host"))


def apply_ldap_defaults_to_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Bos LDAP alanlarini ad.trtekyazilim.com sablonu ile doldur."""
    if not isinstance(data, dict):
        return data

    out = dict(data)
    if ldap_config_is_uninitialized(out):
        for key, value in LDAP_SETTINGS_DEFAULTS.items():
            out[key] = value
        return out

    for key, value in LDAP_SETTINGS_DEFAULTS.items():
        if key == "ldap_enabled":
            continue
        if _is_blank(out.get(key)):
            out[key] = value

    if _is_blank(out.get("auth_mode")):
        out["auth_mode"] = LDAP_AUTH_MODE_DEFAULT

    return out
