"""Phase E E4：偏好服务（统一类型/来源/可信度/删除语义）。

- 显式"记住"或业务流程确认的稳定偏好走本服务写入（决策二门槛由调用方判定）。
- 删除 = 原子删除（墓碑 + 摘要失效 + 消息屏蔽），由 Repository 单事务保证。
- 旧 PreferenceManager/UserBehaviorDBRouter 通过适配器收敛到同一事实来源。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from application.context_contracts import (
    PreferenceDomainError,
    PreferenceRecord,
    PreferenceSourceType,
    PreferenceTypeEnum,
    PreferenceTombstone,
)
from db.repositories.preference_repository import PreferenceRepository

# 旧 user_behavior 组件的偏好类型映射（A-R2 遗留 -> Phase E 统一枚举）
LEGACY_TYPE_MAP = {
    "technician": PreferenceTypeEnum.TECHNICIAN,
    "time": PreferenceTypeEnum.TIME,
    "service": PreferenceTypeEnum.SERVICE,
    "duration": PreferenceTypeEnum.DURATION,
}


class PreferenceService:
    """长期偏好领域服务。"""

    def __init__(self, repository: PreferenceRepository):
        self._repo = repository

    # ---------------- 读取 ----------------

    def list_preferences(self, user_id: str) -> List[Dict[str, Any]]:
        """当前用户全部偏好（管理展示；含 legacy_unverified，但不提升可信度）。"""
        rows = self._repo.get_all_preferences(user_id)
        return [
            PreferenceRecord(
                user_id=r["user_id"],
                preference_type=PreferenceTypeEnum(r["preference_type"]),
                preference_value=r["preference_value"],
                source=PreferenceSourceType(r["source"]),
                source_message_id=r["source_message_id"],
                source_appointment_id=r["source_appointment_id"],
                confidence=r["confidence"] / 100.0 if r["confidence"] is not None else 0.0,
                last_confirmed_at=_parse_dt(r["last_confirmed_at"]),
                expires_at=_parse_dt(r["expires_at"]),
                is_active=r["is_active"],
                deleted_at=_parse_dt(r["deleted_at"]),
                preference_id=r["preference_id"],
            ).to_dict()
            for r in rows
        ]

    def list_active_preferences(self, user_id: str) -> List[PreferenceRecord]:
        """未删除、未过期、来源可信的 active 偏好（ContextBuilder 输入用）。"""
        records: List[PreferenceRecord] = []
        for r in self._repo.get_active_preferences(user_id):
            source = PreferenceSourceType(r["source"])
            if source is PreferenceSourceType.LEGACY_UNVERIFIED:
                continue  # 默认不注入长期上下文（除非用户重新确认提升）
            records.append(
                PreferenceRecord(
                    user_id=r["user_id"],
                    preference_type=PreferenceTypeEnum(r["preference_type"]),
                    preference_value=r["preference_value"],
                    source=source,
                    source_message_id=r["source_message_id"],
                    source_appointment_id=r["source_appointment_id"],
                    confidence=r["confidence"] / 100.0 if r["confidence"] is not None else 0.0,
                    last_confirmed_at=_parse_dt(r["last_confirmed_at"]),
                    expires_at=_parse_dt(r["expires_at"]),
                    is_active=r["is_active"],
                    preference_id=r["preference_id"],
                )
            )
        return records

    # ---------------- 写入（决策二门槛由调用方确认后调用） ----------------

    def set_preference(
        self,
        user_id: str,
        preference_type: str,
        preference_value: str,
        source: str = "explicit_memorize",
        source_message_id: Optional[str] = None,
        source_appointment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """为已确认偏好建立持久化记录（覆盖语义：同类型旧值原子停用）。"""
        try:
            ptype = PreferenceTypeEnum(preference_type)
            psource = PreferenceSourceType(source)
        except ValueError as exc:
            raise PreferenceDomainError("PREFERENCE_INVALID_TYPE", f"未知偏好类型或来源: {exc}")

        if not preference_value or not preference_value.strip():
            raise PreferenceDomainError("PREFERENCE_EMPTY_VALUE", "偏好值不能为空")
        if psource is PreferenceSourceType.LEGACY_UNVERIFIED:
            raise PreferenceDomainError("PREFERENCE_INVALID_SOURCE", "写入路径不允许直接创建未确认来源")

        row = self._repo.set_preference(
            user_id=user_id,
            preference_type=ptype.value,
            preference_value=preference_value.strip(),
            source=psource.value,
            source_message_id=source_message_id,
            source_appointment_id=source_appointment_id,
            confidence=100,
            last_confirmed_at=datetime.utcnow(),
        )
        return PreferenceRecord(
            user_id=row["user_id"],
            preference_type=PreferenceTypeEnum(row["preference_type"]),
            preference_value=row["preference_value"],
            source=PreferenceSourceType(row["source"]),
            source_message_id=row["source_message_id"],
            source_appointment_id=row["source_appointment_id"],
            confidence=row["confidence"] / 100.0,
            last_confirmed_at=_parse_dt(row["last_confirmed_at"]),
            expires_at=_parse_dt(row["expires_at"]),
            is_active=row["is_active"],
            preference_id=row["preference_id"],
        ).to_dict()

    # ---------------- 删除（原子：墓碑+摘要失效+消息屏蔽） ----------------

    def delete_preference(self, user_id: str, preference_type: str) -> Optional[Dict[str, Any]]:
        """删除偏好；不存在时返回 None（幂等成功语义，不泄漏他用户数据）。"""
        try:
            ptype = PreferenceTypeEnum(preference_type)
        except ValueError as exc:
            raise PreferenceDomainError("PREFERENCE_INVALID_TYPE", f"未知偏好类型: {exc}")

        tombstone = self._repo.atomic_delete(user_id, ptype.value)
        return tombstone

    def reconfirm_legacy(self, user_id: str, preference_type: str) -> Optional[Dict[str, Any]]:
        """用户重新确认历史偏好 -> 提升为来源可信的 active 记录（不静默提升）。"""
        legacy = None
        for r in self._repo.get_all_preferences(user_id):
            if r["preference_type"] == preference_type and r["source"] == "legacy_unverified":
                legacy = r
                break
        if legacy is None:
            return None
        return self.set_preference(
            user_id=user_id,
            preference_type=preference_type,
            preference_value=legacy["preference_value"],
            source="explicit_memorize",
        )

    # ---------------- 旧组件适配 ----------------

    def legacy_set(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """旧 UserBehaviorRepository.update_user_preference 的适配入口。

        旧组件不再建立第二套事实：写入统一收敛到本服务（决策四覆盖语义）。
        """
        try:
            self.set_preference(
                user_id=user_id,
                preference_type=preference_type,
                preference_value=preference_value,
                source="explicit_memorize",
            )
            return True
        except PreferenceDomainError:
            return False

    def legacy_get(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """旧 get_user_preferences 的适配入口：只返回可信 active + 未确认历史标记。"""
        return self.list_preferences(user_id)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PreferenceLegacyAdapter:
    """旧 user_behavior 组件的偏好读写适配器（收敛到 PreferenceService）。"""

    def __init__(self, service: PreferenceService):
        self._service = service

    def update_user_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        return self._service.legacy_set(user_id, preference_type, preference_value)

    def get_user_preferences(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._service.list_preferences(user_id)
        if preference_type:
            rows = [r for r in rows if r["preference_type"] == preference_type]
        return rows