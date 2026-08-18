"""Phase G G3：门店结构化配置与审计事务服务。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from db.base.session_manager import SessionManager
from db.models import (
    AuditEvent,
    ServiceCatalogItem,
    Store,
    StoreAppointmentPolicy,
    StoreBusinessHours,
    StoreProfile,
)


class StoreConfigError(ValueError):
    """配置输入或门店边界错误。"""


class StoreConfigService:
    def __init__(self, db_path: Optional[str] = None):
        self.session_manager = SessionManager(db_path)

    def close(self) -> None:
        self.session_manager.close()

    def update_profile(
        self,
        store_id: int,
        actor_id: int,
        values: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._validate_store_id(store_id)
        if "name" in values and not str(values["name"]).strip():
            raise StoreConfigError("门店名称不能为空")
        if "timezone" in values and not str(values["timezone"]).strip():
            raise StoreConfigError("时区不能为空")
        with self.session_manager.session_scope() as session:
            store = session.query(Store).filter_by(id=store_id, is_active=1).first()
            if not store:
                raise StoreConfigError("门店不存在或已停用")
            for field in ("name", "timezone"):
                if field in values:
                    setattr(store, field, values[field])
            profile = session.query(StoreProfile).filter_by(store_id=store_id).first()
            if profile is None:
                profile = StoreProfile(store_id=store_id, is_open=1)
                session.add(profile)
            for field in ("address", "phone", "is_open"):
                if field in values and values[field] is not None:
                    setattr(profile, field, int(values[field]) if field == "is_open" else values[field])
            session.flush()
            self._audit(
                session, actor_id, store_id, "store.profile.updated", "store", store_id,
                request_id, {"fields": sorted(values.keys())},
            )
            return self._profile_dict(store, profile)

    def get_profile(self, store_id: int) -> Dict[str, Any]:
        with self.session_manager.session_scope() as session:
            store = session.query(Store).filter_by(id=store_id, is_active=1).first()
            if not store:
                raise StoreConfigError("门店不存在或已停用")
            profile = session.query(StoreProfile).filter_by(store_id=store_id).first()
            return self._profile_dict(store, profile)

    def create_service(
        self,
        store_id: int,
        actor_id: int,
        values: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = str(values.get("name") or "").strip()
        price = values.get("price_cents")
        duration = values.get("duration_minutes")
        if not name or not isinstance(price, int) or price < 0:
            raise StoreConfigError("服务名称和非负整数价格必填")
        if not isinstance(duration, int) or duration <= 0 or duration > 1440:
            raise StoreConfigError("服务时长必须在 1 到 1440 分钟之间")
        self._validate_store_id(store_id)
        with self.session_manager.session_scope() as session:
            if not session.query(Store).filter_by(id=store_id, is_active=1).first():
                raise StoreConfigError("门店不存在或已停用")
            item = ServiceCatalogItem(
                store_id=store_id,
                name=name,
                price_cents=price,
                duration_minutes=duration,
                description=values.get("description"),
                is_bookable=1 if values.get("is_bookable", True) else 0,
                is_active=1,
            )
            session.add(item)
            session.flush()
            self._audit(
                session, actor_id, store_id, "service.created", "service", item.id,
                request_id, {"name": name, "price_cents": price, "duration_minutes": duration},
            )
            return self._service_dict(item)

    def list_services(self, store_id: int) -> list[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = session.query(ServiceCatalogItem).filter_by(
                store_id=store_id, is_active=1
            ).order_by(ServiceCatalogItem.id).all()
            return [self._service_dict(row) for row in rows]

    def set_business_hours(
        self, store_id: int, actor_id: int, values: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        weekday = values.get("weekday")
        if not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise StoreConfigError("weekday 必须是 0 到 6")
        closed = bool(values.get("is_closed", False))
        open_time = values.get("open_time")
        close_time = values.get("close_time")
        if not closed and (not self._valid_time(open_time) or not self._valid_time(close_time)):
            raise StoreConfigError("营业时间必须使用 HH:MM")
        with self.session_manager.session_scope() as session:
            row = session.query(StoreBusinessHours).filter_by(
                store_id=store_id, weekday=weekday
            ).first()
            if row is None:
                row = StoreBusinessHours(store_id=store_id, weekday=weekday)
                session.add(row)
            row.is_closed = 1 if closed else 0
            row.open_time = None if closed else open_time
            row.close_time = None if closed else close_time
            session.flush()
            self._audit(
                session, actor_id, store_id, "store.business_hours.updated", "business_hours", row.id,
                request_id, {"weekday": weekday, "is_closed": closed},
            )
            return {"store_id": store_id, "weekday": weekday, "open_time": row.open_time,
                    "close_time": row.close_time, "is_closed": bool(row.is_closed)}

    def set_policy(
        self, store_id: int, actor_id: int, values: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        min_notice = values.get("min_notice_minutes", 120)
        cancel_window = values.get("cancel_window_minutes", 120)
        if not isinstance(min_notice, int) or min_notice < 0:
            raise StoreConfigError("提前预约时间必须是非负整数")
        if not isinstance(cancel_window, int) or cancel_window < 0:
            raise StoreConfigError("取消窗口必须是非负整数")
        with self.session_manager.session_scope() as session:
            row = session.query(StoreAppointmentPolicy).filter_by(store_id=store_id).first()
            if row is None:
                row = StoreAppointmentPolicy(store_id=store_id)
                session.add(row)
            row.min_notice_minutes = min_notice
            row.cancel_window_minutes = cancel_window
            row.late_rule = values.get("late_rule")
            session.flush()
            self._audit(
                session, actor_id, store_id, "store.policy.updated", "appointment_policy", row.id,
                request_id, {"min_notice_minutes": min_notice, "cancel_window_minutes": cancel_window},
            )
            return {"store_id": store_id, "min_notice_minutes": row.min_notice_minutes,
                    "cancel_window_minutes": row.cancel_window_minutes, "late_rule": row.late_rule}

    def list_audit(self, store_id: int, limit: int = 100) -> list[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = session.query(AuditEvent).filter_by(store_id=store_id).order_by(
                AuditEvent.created_at.desc()
            ).limit(min(max(limit, 1), 500)).all()
            return [self._audit_dict(row) for row in rows]

    @staticmethod
    def _audit(
        session, actor_id: int, store_id: int, action: str, resource_type: str,
        resource_id: Any, request_id: Optional[str], summary: Dict[str, Any],
    ) -> None:
        session.add(AuditEvent(
            id=secrets.token_urlsafe(24), actor_id=actor_id, store_id=store_id,
            action=action, resource_type=resource_type, resource_id=str(resource_id),
            request_id=request_id, outcome="succeeded",
            summary_json=json.dumps(summary, ensure_ascii=False), created_at=datetime.utcnow(),
        ))

    @staticmethod
    def _validate_store_id(store_id: int) -> None:
        if not isinstance(store_id, int) or store_id <= 0:
            raise StoreConfigError("门店上下文无效")

    @staticmethod
    def _valid_time(value: Any) -> bool:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            return False
        try:
            hour, minute = (int(part) for part in value.split(":"))
            return 0 <= hour <= 23 and 0 <= minute <= 59
        except ValueError:
            return False

    @staticmethod
    def _profile_dict(store, profile) -> Dict[str, Any]:
        return {
            "store_id": store.id, "name": store.name, "timezone": store.timezone,
            "address": profile.address if profile else None,
            "phone": profile.phone if profile else None,
            "is_open": bool(profile.is_open) if profile else True,
        }

    @staticmethod
    def _service_dict(item) -> Dict[str, Any]:
        return {
            "service_id": item.id, "store_id": item.store_id, "name": item.name,
            "price_cents": item.price_cents, "duration_minutes": item.duration_minutes,
            "description": item.description, "is_bookable": bool(item.is_bookable),
        }

    @staticmethod
    def _audit_dict(row) -> Dict[str, Any]:
        try:
            summary = json.loads(row.summary_json) if row.summary_json else {}
        except (TypeError, ValueError):
            summary = {}
        return {
            "event_id": row.id, "actor_id": row.actor_id, "store_id": row.store_id,
            "action": row.action, "resource_type": row.resource_type,
            "resource_id": row.resource_id, "request_id": row.request_id,
            "outcome": row.outcome, "summary": summary,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
