"""Phase G G1：商家角色到 capability 的集中定义。"""

from typing import FrozenSet


ADMIN_PERMISSIONS = frozenset(
    {
        "view_sessions",
        "manage_conversations",
        "view_knowledge",
        "publish_knowledge",
        "manage_store",
        "write_appointments",
        "read_customer_preferences",
        "manage_members",
        "read_members",
        "read_audit",
        "manage_customer_data",  # Phase I I2：客户数据导出/删除/匿名化（仅 owner/manager）
    }
)

ROLE_PERMISSIONS: dict[str, FrozenSet[str]] = {
    "owner": frozenset(ADMIN_PERMISSIONS),
    "manager": frozenset(
        {
            "view_sessions",
            "manage_conversations",
            "view_knowledge",
            "publish_knowledge",
            "manage_store",
            "write_appointments",
            "read_customer_preferences",
            "read_members",
            "read_audit",
            "manage_customer_data",
        }
    ),
    "operator": frozenset(
        {
            "view_sessions",
            "manage_conversations",
            "view_knowledge",
            "write_appointments",
            "read_customer_preferences",
        }
    ),
    "viewer": frozenset(
        {
            "view_sessions",
            "view_knowledge",
            "read_customer_preferences",
        }
    ),
}


def has_permission(role: str, permission: str) -> bool:
    """未知角色/权限默认拒绝。"""
    if permission not in ADMIN_PERMISSIONS:
        return False
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
