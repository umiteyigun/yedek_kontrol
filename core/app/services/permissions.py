"""Modul bazli panel yetkileri — yalnizca yerel kullanicilar icin ozel matris."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED

ACTIONS = ("view", "add", "edit", "delete")

ACTION_LABELS = {
    "view": "Gor",
    "add": "Ekle",
    "edit": "Duzenle",
    "delete": "Sil",
}

# Her modul icin hangi aksiyonlar anlamlidir
MODULES: dict[str, dict[str, Any]] = {
    "dashboard": {"label": "Durum", "actions": ("view",)},
    "backups": {"label": "Yedekler", "actions": ACTIONS},
    "rman": {"label": "RMAN", "actions": ACTIONS},
    "settings": {"label": "Ayarlar", "actions": ACTIONS},
    "system": {"label": "Sistem", "actions": ACTIONS},
    "tablespaces": {"label": "Tablespace", "actions": ("view", "add")},
    "terminal": {"label": "Terminal", "actions": ("view",)},
}

MODULE_ORDER = tuple(MODULES.keys())


def _all_true() -> dict[str, dict[str, bool]]:
    return {
        module: {action: True for action in MODULES[module]["actions"]}
        for module in MODULES
    }


def _none_module() -> dict[str, bool]:
    return {action: False for action in ACTIONS}


ROLE_DEFAULTS: dict[str, dict[str, dict[str, bool]]] = {
    ROLE_FULL: _all_true(),
    ROLE_LIMITED: {
        "dashboard": {"view": True},
        "backups": {"view": True, "add": True, "edit": True, "delete": False},
        "rman": _none_module(),
        "settings": _none_module(),
        "system": _none_module(),
        "tablespaces": {"view": False, "add": False},
        "terminal": {"view": False},
    },
}


def empty_permissions() -> dict[str, dict[str, bool]]:
    return {
        module: {action: False for action in MODULES[module]["actions"]}
        for module in MODULES
    }


def normalize_permissions(raw: dict[str, Any] | None) -> dict[str, dict[str, bool]]:
    base = empty_permissions()
    if not raw:
        return base
    for module, actions in raw.items():
        if module not in MODULES or not isinstance(actions, dict):
            continue
        allowed = MODULES[module]["actions"]
        for action in allowed:
            if action in actions:
                base[module][action] = bool(actions[action])
    return base


def permissions_for_role(role: str) -> dict[str, dict[str, bool]]:
    defaults = ROLE_DEFAULTS.get(role, ROLE_DEFAULTS[ROLE_LIMITED])
    return normalize_permissions(defaults)


def parse_permissions_from_form(form: Any) -> dict[str, dict[str, bool]]:
    perms = empty_permissions()
    for module in MODULES:
        for action in MODULES[module]["actions"]:
            key = f"perm_{module}_{action}"
            perms[module][action] = form.get(key) == "1"
    return perms


def has_permission(
    perms: dict[str, dict[str, bool]],
    module: str,
    action: str,
) -> bool:
    if module not in MODULES:
        return False
    if action not in MODULES[module]["actions"]:
        return False
    return bool(perms.get(module, {}).get(action))


def resolve_permissions(
    *,
    auth_method: str,
    role: str,
    username: str,
    local_user_store: Any | None,
    local_role_store: Any | None = None,
) -> dict[str, dict[str, bool]]:
    if auth_method == "local":
        if local_role_store is not None:
            return local_role_store.get_permissions(role)
        row = local_user_store.get_user(username) if local_user_store else None
        if row and row.get("permissions"):
            return normalize_permissions(row["permissions"])
    return permissions_for_role(role)


def get_request_permissions(request: Request) -> dict[str, dict[str, bool]]:
    cached = getattr(request.state, "effective_permissions", None)
    if cached is not None:
        return cached

    from app.auth import get_session

    session = get_session(request)
    if not session:
        perms = empty_permissions()
    else:
        local_store = getattr(request.app.state, "local_user_store", None)
        role_store = getattr(request.app.state, "local_role_store", None)
        perms = resolve_permissions(
            auth_method=str(session.get("auth") or ""),
            role=str(session.get("role") or ROLE_LIMITED),
            username=str(session.get("user") or ""),
            local_user_store=local_store,
            local_role_store=role_store,
        )
    request.state.effective_permissions = perms
    return perms


def can(request: Request, module: str, action: str) -> bool:
    return has_permission(get_request_permissions(request), module, action)


def nav_flags(request: Request) -> dict[str, bool]:
    perms = get_request_permissions(request)
    return {
        "can_view_dashboard": has_permission(perms, "dashboard", "view"),
        "can_view_backups": has_permission(perms, "backups", "view"),
        "can_view_rman": has_permission(perms, "rman", "view"),
        "can_view_settings": has_permission(perms, "settings", "view"),
        "can_view_system": has_permission(perms, "system", "view"),
        "can_view_tablespaces": has_permission(perms, "tablespaces", "view"),
        "can_tablespaces_add": has_permission(perms, "tablespaces", "add"),
        "can_view_terminal": has_permission(perms, "terminal", "view"),
        "can_backup_add": has_permission(perms, "backups", "add"),
        "can_backup_edit": has_permission(perms, "backups", "edit"),
        "can_backup_delete": has_permission(perms, "backups", "delete"),
        "can_rman_edit": has_permission(perms, "rman", "edit"),
        "can_rman_delete": has_permission(perms, "rman", "delete"),
        "can_settings_edit": has_permission(perms, "settings", "edit"),
        "can_settings_delete": has_permission(perms, "settings", "delete"),
        "can_system_edit": has_permission(perms, "system", "edit"),
    }
