"""Phase G G1：商家 RBAC 权限矩阵契约。"""

import pytest

from application.admin_permissions import (
    ADMIN_PERMISSIONS,
    has_permission,
)


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        ("owner", "view_sessions", True),
        ("owner", "manage_members", True),
        ("manager", "publish_knowledge", True),
        ("manager", "manage_members", False),
        ("operator", "write_appointments", True),
        ("operator", "publish_knowledge", False),
        ("operator", "read_audit", False),
        ("viewer", "view_sessions", True),
        ("viewer", "write_appointments", False),
        ("viewer", "read_customer_preferences", True),
    ],
)
def test_角色权限矩阵(role, permission, expected):
    assert permission in ADMIN_PERMISSIONS
    assert has_permission(role, permission) is expected


def test_未知角色和权限默认拒绝():
    assert has_permission("unknown", "view_sessions") is False
    assert has_permission("owner", "unknown") is False
