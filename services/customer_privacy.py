"""Phase I I2：客户数据导出与删除/匿名化（D9 商家侧 / D10 删除登记 / D11 文件安全）。

- 操作者限定为已登录商家（RBAC + store scope + CSRF 由 API 层保证）。
- export_customer：在门店范围内收集客户的 PII 明细，写入短时一次性导出记录。
- anonymize_customer：默认 dry-run；真实执行时在单事务内匿名化各实体并写删除登记，
  request_id 幂等。已完成/确认预约作为业务与审计事实保留（去标识原因见 I0 D4/D10），
  前端文本一律替换为占位符，不硬删业务结构。
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import bindparam, text

from db.base.session_manager import SessionManager

ANON = "[已删除]"
_EXPORT_TTL_SECONDS = 300  # 导出下载令牌 5 分钟有效


class CustomerPrivacyError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _ids_bind():
    return bindparam("ids", expanding=True)


class CustomerPrivacyService:
    """客户隐私服务（导出/删除/匿名化）。"""

    def __init__(self, db_path: str | None = None):
        self.sm = SessionManager(db_path)
        self._exports: Dict[str, Dict[str, Any]] = {}

    def close(self) -> None:
        self.sm.close()

    # ---------------- 实体盘点（D14）----------------

    def _conversation_ids(self, session, store_id: int, user_id: str) -> List[str]:
        rows = session.execute(text(
            "SELECT id FROM conversations "
            "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL)"
        ), {"u": user_id, "s": store_id}).fetchall()
        return [str(r[0]) for r in rows]

    def collect_plan(self, store_id: int, customer_user_id: str) -> Dict[str, int]:
        """汇总各实体命中数（dry-run / 导出共用）。"""
        with self.sm.session_scope() as session:
            cids = self._conversation_ids(session, store_id, customer_user_id)
            plan: Dict[str, int] = {
                "conversations": 0, "messages": 0, "conversation_summaries": 0,
            }
            if cids:
                plan["conversations"] = len(cids)
                plan["messages"] = session.execute(
                    text("SELECT COUNT(*) FROM messages WHERE conversation_id IN :ids")
                    .bindparams(_ids_bind()),
                    {"ids": cids},
                ).scalar()
                plan["conversation_summaries"] = session.execute(
                    text("SELECT COUNT(*) FROM conversation_summaries WHERE conversation_id IN :ids")
                    .bindparams(_ids_bind()),
                    {"ids": cids},
                ).scalar()
            plan["preferences"] = session.execute(
                text("SELECT COUNT(*) FROM preferences "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL)"),
                {"u": customer_user_id, "s": store_id},
            ).scalar()
            plan["legacy_user_preferences"] = session.execute(
                text("SELECT COUNT(*) FROM user_preferences WHERE user_id=:u"),
                {"u": customer_user_id},
            ).scalar()
            plan["follow_up_tasks"] = session.execute(
                text("SELECT COUNT(*) FROM follow_up_tasks "
                     "WHERE customer_user_id=:u AND store_id=:s"),
                {"u": customer_user_id, "s": store_id},
            ).scalar()
            plan["user_behaviors"] = session.execute(
                text("SELECT COUNT(*) FROM user_behaviors "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL)"),
                {"u": customer_user_id, "s": store_id},
            ).scalar()
            plan["user_recommendations"] = session.execute(
                text("SELECT COUNT(*) FROM user_recommendations WHERE user_id=:u"),
                {"u": customer_user_id},
            ).scalar()
            # 已完成/确认预约作为业务审计事实保留（去标识原因见 I0 D4/D10）
            plan["appointments_retained"] = session.execute(
                text("SELECT COUNT(*) FROM appointments "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL) "
                     "AND status IN ('confirmed','expired','cancelled')"),
                {"u": customer_user_id, "s": store_id},
            ).scalar()
            return plan

    # ---------------- 导出（E7 / D11）----------------

    def create_export(self, store_id: int, customer_user_id: str) -> Dict[str, Any]:
        """在门店范围内收集客户 PII，生成短时一次性导出记录。"""
        with self.sm.session_scope() as session:
            cids = self._conversation_ids(session, store_id, customer_user_id)
            messages = []
            if cids:
                rows = session.execute(
                    text("SELECT c.id, m.role, m.sequence, m.content "
                         "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                         "WHERE m.conversation_id IN :ids ORDER BY c.id, m.sequence")
                    .bindparams(_ids_bind()),
                    {"ids": cids},
                ).fetchall()
                messages = [
                    {"conversation_id": r[0], "role": r[1], "sequence": r[2], "content": r[3]}
                    for r in rows
                ]
        plan = self.collect_plan(store_id, customer_user_id)

        export_id = secrets.token_urlsafe(16)
        token = secrets.token_urlsafe(24)
        self._exports[export_id] = {
            "token": token,
            "expires_at": time.time() + _EXPORT_TTL_SECONDS,
            "data": {
                "store_id": store_id,
                "customer_user_id": customer_user_id,
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "counts": plan,
                "messages": messages,
            },
        }
        return {"export_id": export_id, "expires_in_seconds": _EXPORT_TTL_SECONDS,
                "download_token": token, "counts": plan}

    def consume_export(self, export_id: str, token: str) -> Dict[str, Any]:
        """一次性领取导出文件；令牌错误/过期/已领用则拒绝。"""
        rec = self._exports.get(export_id)
        if not rec or not secrets.compare_digest(rec["token"], token):
            raise CustomerPrivacyError("EXPORT_INVALID", "导出已过期或令牌无效")
        if time.time() > rec["expires_at"]:
            self._exports.pop(export_id, None)
            raise CustomerPrivacyError("EXPORT_INVALID", "导出已过期，请重新发起导出")
        self._exports.pop(export_id, None)  # 一次性
        return rec["data"]

    # ---------------- 删除 / 匿名化（E8 / D9 / D10）----------------

    def anonymize_customer(self, store_id: int, customer_user_id: str, *,
                           actor_id: int, request_id: str, dry_run: bool = True) -> Dict[str, Any]:
        if not request_id:
            raise CustomerPrivacyError("MISSING_REQUEST_ID", "删除请求需提供幂等 request_id")

        plan = self.collect_plan(store_id, customer_user_id)

        with self.sm.session_scope() as session:
            existing = session.execute(
                text("SELECT id FROM privacy_deletion_registry WHERE request_id=:r"),
                {"r": request_id},
            ).scalar()
            if existing is not None:
                return {"idempotent_replay": True, "request_id": request_id,
                        "store_id": store_id, **plan}

        if dry_run:
            return {"dry_run": True, "request_id": request_id,
                    "store_id": store_id, **plan}

        with self.sm.session_scope() as session:
            cids = self._conversation_ids(session, store_id, customer_user_id)
            if cids:
                session.execute(
                    text("UPDATE messages SET content=:a WHERE conversation_id IN :ids")
                    .bindparams(bindparam("a"), _ids_bind()),
                    {"a": ANON, "ids": cids},
                )
                session.execute(
                    text("UPDATE conversation_summaries SET status='invalidated', content=:a "
                         "WHERE conversation_id IN :ids AND status='active'")
                    .bindparams(bindparam("a"), _ids_bind()),
                    {"a": ANON, "ids": cids},
                )
            session.execute(
                text("UPDATE preferences SET is_active=0, deleted_at=:now "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL) AND is_active=1"),
                {"now": datetime.utcnow(), "u": customer_user_id, "s": store_id},
            )
            session.execute(
                text("DELETE FROM user_preferences WHERE user_id=:u"),
                {"u": customer_user_id},
            )
            session.execute(
                text("UPDATE follow_up_tasks SET reason=:a "
                     "WHERE customer_user_id=:u AND store_id=:s"),
                {"a": ANON, "u": customer_user_id, "s": store_id},
            )
            session.execute(
                text("UPDATE user_behaviors SET action_data=:a "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL)"),
                {"a": ANON, "u": customer_user_id, "s": store_id},
            )
            session.execute(
                text("UPDATE user_recommendations SET content=:a WHERE user_id=:u"),
                {"a": ANON, "u": customer_user_id},
            )
            # 业务审计事实：预约仅去标识自由文本，保留记录（D4）
            session.execute(
                text("UPDATE appointments SET cancel_reason=:a "
                     "WHERE user_id=:u AND (store_id=:s OR store_id IS NULL)"),
                {"a": ANON, "u": customer_user_id, "s": store_id},
            )
            session.execute(
                text("INSERT INTO privacy_deletion_registry "
                     "(store_id, customer_user_id, request_id, entity_counts_json, actor_id, created_at) "
                     "VALUES (:s, :u, :r, :c, :a, :now)"),
                {
                    "s": store_id, "u": customer_user_id, "r": request_id,
                    "c": json.dumps(plan, ensure_ascii=False), "a": actor_id,
                    "now": datetime.utcnow(),
                },
            )

        return {"dry_run": False, "anonymized": True, "request_id": request_id,
                "store_id": store_id, **plan}
