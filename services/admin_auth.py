"""Phase G G1：商家账号、服务端会话和门店 membership。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import delete

from application.admin_permissions import ROLE_PERMISSIONS
from db.base.session_manager import SessionManager
from db.models import (
    AdminSession,
    AuditEvent,
    ConversationControl,
    ConversationControlEvent,
    MerchantAccount,
    Store,
    StoreMembership,
)


SESSION_COOKIE_NAME = "admin_session"
SESSION_TTL_HOURS = 8
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_DUMMY_SALT = b"AIFrontDesk-G1-dummy-salt"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_hash(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
    )
    return "scrypt${}${}${}${}${}".format(
        _PASSWORD_N,
        _PASSWORD_R,
        _PASSWORD_P,
        _b64(salt),
        _b64(digest),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual, _unb64(expected))
    except (TypeError, ValueError):
        return False


_DUMMY_PASSWORD_HASH = _password_hash("invalid-password", _DUMMY_SALT)


class AdminAuthError(Exception):
    """商家身份服务内部错误，API 层负责映射稳定错误码。"""


class AdminAuthService:
    """商家认证服务；所有身份和 active store 信息来自服务端数据库。"""

    def __init__(self, db_path: Optional[str] = None):
        self.session_manager = SessionManager(db_path)

    def close(self) -> None:
        self.session_manager.close()

    def clear_for_tests(self) -> None:
        """仅供测试清理 G1 表，不作为生产 API。"""
        with self.session_manager.session_scope() as session:
            session.execute(delete(AdminSession))
            session.execute(delete(AuditEvent))
            session.execute(delete(ConversationControlEvent))
            session.execute(delete(ConversationControl))
            session.execute(delete(StoreMembership))
            session.execute(delete(MerchantAccount))

    def provision_account(
        self,
        username: str,
        password: str,
        display_name: str,
        store_name: str,
        role: str = "owner",
    ) -> Dict[str, Any]:
        """创建账号、默认门店和首个 membership；不暴露为 HTTP 注册接口。"""
        self._validate_role(role)
        if not username or not password or not display_name or not store_name:
            raise ValueError("账号、密码、显示名和门店名均不能为空")
        with self.session_manager.session_scope() as session:
            if session.query(MerchantAccount).filter_by(username=username).first():
                raise ValueError("账号已存在")
            store = Store(name=store_name)
            account = MerchantAccount(
                username=username,
                password_hash=_password_hash(password),
                display_name=display_name,
                is_active=1,
            )
            session.add_all([store, account])
            session.flush()
            session.add(
                StoreMembership(
                    actor_id=account.id,
                    store_id=store.id,
                    role=role,
                    is_active=1,
                )
            )
            return {"actor_id": account.id, "store_id": store.id}

    def create_store(self, name: str, timezone: str = "Asia/Shanghai") -> Dict[str, Any]:
        if not name:
            raise ValueError("门店名不能为空")
        with self.session_manager.session_scope() as session:
            store = Store(name=name, timezone=timezone, is_active=1)
            session.add(store)
            session.flush()
            return {"store_id": store.id, "name": store.name, "timezone": store.timezone}

    def add_membership(self, actor_id: int, store_id: int, role: str) -> None:
        self._validate_role(role)
        with self.session_manager.session_scope() as session:
            if not session.get(MerchantAccount, actor_id) or not session.get(Store, store_id):
                raise ValueError("账号或门店不存在")
            membership = session.query(StoreMembership).filter_by(
                actor_id=actor_id, store_id=store_id
            ).first()
            if membership:
                membership.role = role
                membership.is_active = 1
            else:
                session.add(StoreMembership(
                    actor_id=actor_id, store_id=store_id, role=role, is_active=1
                ))

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            account = session.query(MerchantAccount).filter_by(username=username).first()
            password_ok = _verify_password(
                password,
                account.password_hash if account else _DUMMY_PASSWORD_HASH,
            )
            if not account or not account.is_active or not password_ok:
                return None
            memberships = self._memberships(session, account.id)
            if not memberships:
                return None
            raw_token = secrets.token_urlsafe(32)
            raw_csrf = secrets.token_urlsafe(32)
            first_store = memberships[0]["store_id"]
            now = datetime.utcnow()
            session.add(AdminSession(
                id=secrets.token_urlsafe(24),
                token_hash=_token_hash(raw_token),
                csrf_token_hash=_token_hash(raw_csrf),
                actor_id=account.id,
                active_store_id=first_store,
                expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
                created_at=now,
                last_seen_at=now,
            ))
            identity = self._identity_payload(session, account, memberships, first_store)
            identity["session_token"] = raw_token
            identity["csrf_token"] = raw_csrf
            return identity

    def resolve_session(self, raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not raw_token:
            return None
        with self.session_manager.session_scope() as session:
            row = session.query(AdminSession).filter_by(token_hash=_token_hash(raw_token)).first()
            if not row or row.revoked_at is not None or row.expires_at <= datetime.utcnow():
                return None
            account = session.get(MerchantAccount, row.actor_id)
            if not account or not account.is_active:
                return None
            memberships = self._memberships(session, account.id)
            if not memberships:
                return None
            store_ids = {item["store_id"] for item in memberships}
            if row.active_store_id not in store_ids:
                row.active_store_id = memberships[0]["store_id"]
            row.last_seen_at = datetime.utcnow()
            return self._identity_payload(session, account, memberships, row.active_store_id)

    def verify_csrf(self, raw_token: Optional[str], csrf_token: Optional[str]) -> bool:
        if not raw_token or not csrf_token:
            return False
        with self.session_manager.session_scope() as session:
            row = session.query(AdminSession).filter_by(token_hash=_token_hash(raw_token)).first()
            if not row or row.revoked_at is not None or row.expires_at <= datetime.utcnow():
                return False
            return hmac.compare_digest(row.csrf_token_hash, _token_hash(csrf_token))

    def switch_store(self, raw_token: str, store_id: int) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            row = session.query(AdminSession).filter_by(token_hash=_token_hash(raw_token)).first()
            if not row or row.revoked_at is not None or row.expires_at <= datetime.utcnow():
                return None
            membership = session.query(StoreMembership).filter_by(
                actor_id=row.actor_id, store_id=store_id, is_active=1
            ).first()
            store = session.query(Store).filter_by(id=store_id, is_active=1).first()
            account = session.get(MerchantAccount, row.actor_id)
            if not membership or not store or not account or not account.is_active:
                return None
            row.active_store_id = store_id
            row.last_seen_at = datetime.utcnow()
            return self._identity_payload(
                session, account, self._memberships(session, account.id), store_id
            )

    def revoke(self, raw_token: Optional[str]) -> None:
        if not raw_token:
            return
        with self.session_manager.session_scope() as session:
            row = session.query(AdminSession).filter_by(token_hash=_token_hash(raw_token)).first()
            if row and row.revoked_at is None:
                row.revoked_at = datetime.utcnow()

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in ROLE_PERMISSIONS:
            raise ValueError(f"未知商家角色: {role}")

    @staticmethod
    def _memberships(session, actor_id: int) -> list[Dict[str, Any]]:
        rows = (
            session.query(StoreMembership, Store)
            .join(Store, Store.id == StoreMembership.store_id)
            .filter(
                StoreMembership.actor_id == actor_id,
                StoreMembership.is_active == 1,
                Store.is_active == 1,
            )
            .order_by(Store.id)
            .all()
        )
        return [
            {
                "store_id": membership.store_id,
                "name": store.name,
                "timezone": store.timezone,
                "role": membership.role,
            }
            for membership, store in rows
        ]

    @staticmethod
    def _identity_payload(session, account, memberships, active_store_id) -> Dict[str, Any]:
        active = next((m for m in memberships if m["store_id"] == active_store_id), None)
        return {
            "actor": {
                "actor_id": account.id,
                "username": account.username,
                "display_name": account.display_name,
            },
            "stores": memberships,
            "active_store": active,
            "role": active["role"] if active else None,
        }
