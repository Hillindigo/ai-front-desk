"""Phase E E5：真实数据读取器适配器（ContextBuilder 的注入端）。

把 Repository / 领域服务适配为 context_builder 的只读读取器接口；
读取器只读、不写库、不携带长期 Session（每次读取短生命周期事务）。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from application.context_builder import (
    AppointmentReader,
    EvidenceReader,
    MessageReader,
    PreferenceReader,
    SummaryReader,
)
from application.context_contracts import (
    KNOWLEDGE_MIN_SCORE_DEFAULT,
    PreferenceRecord,
    PreferenceSourceType,
    PreferenceTypeEnum,
    RetrievedEvidence,
    SummarySnapshot,
    SummaryStatus,
)
from db.repositories.summary_repository import SummaryRepository

logger = logging.getLogger(__name__)


class RepositoryMessageReader(MessageReader):
    """会话消息读取（sequence 增量；过滤墓碑屏蔽消息由调用方负责）。"""

    def __init__(self, conversation_repository):
        self._conversations = conversation_repository

    def recent_messages(
        self,
        conversation_id: str,
        after_sequence: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if after_sequence is not None:
            rows = self._conversations.get_messages_after(conversation_id, after_sequence)
        else:
            rows = self._conversations.get_recent_messages(conversation_id, limit=limit or 100)
        if limit is not None and after_sequence is not None and len(rows) > limit:
            rows = rows[-limit:]
        return rows


class RepositoryAppointmentReader(AppointmentReader):
    """活跃预约/草稿事实（以 Phase C 领域数据为事实来源）。"""

    def __init__(self, appointment_repository):
        self._appointments = appointment_repository

    def active_facts(self, conversation_id: str) -> Dict[str, Any]:
        draft = self._appointments.get_active_draft(conversation_id)
        if draft is not None:
            return {
                "status": draft.get("status"),
                "service_type": draft.get("service_type"),
                "project": draft.get("project"),
                "start_time": draft.get("start_time"),
                "end_time": draft.get("end_time"),
                "duration_minutes": draft.get("duration_minutes"),
                "appointment_id": draft.get("id"),
            }
        return {}


class RepositorySummaryReader(SummaryReader):
    """最新有效摘要读取（ACTIVE 快照 -> SummarySnapshot）。"""

    def __init__(self, summary_repository: SummaryRepository):
        self._repo = summary_repository

    def latest_usable(self, conversation_id: str) -> Optional[SummarySnapshot]:
        row = self._repo.get_latest_active(conversation_id)
        if row is None:
            return None
        try:
            return SummarySnapshot(
                conversation_id=row["conversation_id"],
                from_sequence=row["from_sequence"],
                to_sequence=row["to_sequence"],
                content=row["content"],
                key_facts=row.get("key_facts") or [],
                status=SummaryStatus(row["status"]),
                version=row["version"],
                model_provider=row.get("model_provider", "fake"),
                failure_log_id=row.get("failure_log_id"),
                summary_id=row.get("summary_id"),
            )
        except Exception:
            logger.warning("摘要快照损坏，跳过：conv=%s v=%s", conversation_id, row.get("version"))
            return None


class ServicePreferenceReader(PreferenceReader):
    """可信偏好读取（PreferenceService 已过滤 legacy_unverified 与删除项）。"""

    def __init__(self, preference_service):
        self._service = preference_service

    def confirmed_preferences(self, user_id: str) -> List[PreferenceRecord]:
        try:
            return self._service.list_active_preferences(user_id)
        except Exception:
            logger.exception("偏好读取失败（旁路）")
            return []


class KnowledgeEvidenceReader(EvidenceReader):
    """知识证据读取（E6：KnowledgeService 已保证阈值过滤与索引快照一致性）。"""

    def __init__(self, knowledge_service, min_score: float = KNOWLEDGE_MIN_SCORE_DEFAULT):
        self._knowledge = knowledge_service
        self._min_score = min_score

    async def retrieve(self, query: str, limit: int) -> List[RetrievedEvidence]:
        try:
            results = await self._knowledge.search_structured(query, top_k=limit)
        except Exception:
            logger.exception("知识检索失败（旁路降级），返回无依据结果")
            return []
        evidence: List[RetrievedEvidence] = []
        for row in results or []:
            score = float(row.get("score", 0.0))
            if score < self._min_score:  # 双保险：服务端阈值之外的兜底
                continue
            try:
                evidence.append(
                    RetrievedEvidence(
                        document_id=int(row["document_id"]),
                        category=str(row.get("category", "")),
                        snippet=str(row.get("snippet", "")),
                        score=score,
                        source_version=str(row.get("source_version", "index-0")),
                        rank=int(row.get("rank", len(evidence) + 1)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                logger.warning("检索结果缺少必需字段，跳过一条")
                continue
            if len(evidence) >= limit:
                break
        return evidence