import logging
import re

from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPException

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED
from app.config.models import YedekSettings
from app.services.ldap_settings import EffectiveLdapConfig, effective_ldap_config

logger = logging.getLogger(__name__)


def _group_name_from_dn(dn: str) -> str:
    match = re.search(r"cn=([^,]+)", dn, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _resolve_role(group_names: set[str], cfg: EffectiveLdapConfig) -> str | None:
    if group_names & set(cfg.groups_full):
        return ROLE_FULL
    if group_names & set(cfg.groups_limited):
        return ROLE_LIMITED
    return None


def _fetch_groups(conn: Connection, user_dn: str, cfg: EffectiveLdapConfig) -> set[str]:
    names: set[str] = set()
    conn.search(user_dn, "(objectClass=*)", attributes=["memberOf"])
    if conn.entries:
        attrs = conn.entries[0].entry_attributes_as_dict
        for dn in attrs.get("memberOf", []):
            name = _group_name_from_dn(str(dn))
            if name:
                names.add(name)
    if names:
        return names

    conn.search(
        cfg.group_base,
        f"(&(objectClass=groupOfNames)(member={user_dn}))",
        attributes=["cn"],
    )
    for entry in conn.entries:
        if "cn" in entry:
            names.add(str(entry.cn.value).lower())
    return names


def ldap_login(username: str, password: str, settings: YedekSettings | None = None) -> tuple[bool, str | None]:
    """LDAP/FreeIPA giris + grup yetkisi. Donus: (basarili, role)."""
    if not username or not password:
        return False, None

    cfg = effective_ldap_config(settings) if settings else effective_ldap_config(YedekSettings())
    if not cfg.enabled:
        return False, None

    user_dn = cfg.user_dn_template.format(username=username)
    try:
        server = Server(cfg.host, port=cfg.port, use_ssl=cfg.use_ssl, get_info=ALL)
        with Connection(server, user=user_dn, password=password, auto_bind=True) as conn:
            groups = _fetch_groups(conn, user_dn, cfg)
            role = _resolve_role(groups, cfg)
            if role is None:
                logger.info("LDAP giris reddedildi (%s): uygun grup yok %s", username, groups)
                return False, None
            logger.info("LDAP giris OK (%s) role=%s gruplar=%s", username, role, groups)
            return True, role
    except LDAPException as exc:
        logger.info("LDAP giris basarisiz (%s): %s", username, exc)
        return False, None
    except Exception:
        logger.exception("LDAP beklenmeyen hata (%s)", username)
        return False, None


def test_ldap_connection(settings: YedekSettings) -> tuple[bool, str]:
    cfg = effective_ldap_config(settings)
    if not cfg.host:
        return False, "LDAP sunucu adresi bos"
    try:
        server = Server(cfg.host, port=cfg.port, use_ssl=cfg.use_ssl, get_info=ALL, connect_timeout=8)
        conn = Connection(server, auto_bind=True)
        conn.unbind()
        return True, f"Baglanti OK: {cfg.host}:{cfg.port}"
    except LDAPException as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
