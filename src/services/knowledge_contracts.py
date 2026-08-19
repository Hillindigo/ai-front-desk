"""Phase F F1：知识治理契约 —— 状态机、错误码与领域数据类。

定义知识文档从草稿到发布/归档的状态机、允许迁移、发布结果契约，以及
Phase E 检索证据之上的知识版本化字段。本模块是纯契约（无 IO、无 ORM），
供仓储、服务与 API 共同遵循，保证草案/发布/归档语义唯一。

状态取值与计划 2.1 一致：
    draft     可编辑、可预览，不作为正式回答依据
    published 已发布，可被正式检索和引用
    archived  已归档，不再作为新回答依据，但保留历史
    failed    发布/索引失败，保留上一份 published
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class KnowledgeStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    FAILED = "failed"


# 状态机：允许的迁移表（纯函数判定，禁止非法跳转）。
ALLOWED_TRANSITIONS: Dict[KnowledgeStatus, set] = {
    KnowledgeStatus.DRAFT: {KnowledgeStatus.PUBLISHED, KnowledgeStatus.ARCHIVED},
    KnowledgeStatus.PUBLISHED: {KnowledgeStatus.DRAFT, KnowledgeStatus.ARCHIVED},
    KnowledgeStatus.ARCHIVED: {KnowledgeStatus.PUBLISHED, KnowledgeStatus.DRAFT},
    KnowledgeStatus.FAILED: {KnowledgeStatus.DRAFT, KnowledgeStatus.PUBLISHED},
}


def can_transition(current: KnowledgeStatus, target: KnowledgeStatus) -> bool:
    """判断 status 从 current -> target 是否合法。"""
    return target in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: KnowledgeStatus, target: KnowledgeStatus) -> None:
    """校验迁移；非法迁移抛 InvalidStateTransitionError。"""
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"非法状态迁移：{current.value} -> {target.value}"
        )


# ---------------- 知识领域异常（错误码契约） ----------------

class KnowledgeError(Exception):
    """知识治理领域异常基类。"""

    code = "INTERNAL_ERROR"

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class InvalidKnowledgeInputError(KnowledgeError):
    code = "INVALID_INPUT"


class KnowledgeNotFoundError(KnowledgeError):
    code = "KNOWLEDGE_NOT_FOUND"


class InvalidStateTransitionError(KnowledgeError):
    code = "INVALID_STATE_TRANSITION"


class IndexBuildFailedError(KnowledgeError):
    code = "INDEX_BUILD_FAILED"


class KnowledgeVersionConflictError(KnowledgeError):
    code = "KNOWLEDGE_VERSION_CONFLICT"


class KnowledgeNotReadyError(KnowledgeError):
    code = "KNOWLEDGE_NOT_READY"


# ---------------- 领域数据类 ----------------

@dataclass(frozen=True)
class KnowledgeDocumentContract:
    """知识文档面向 API 的稳定契约（不暴露 embedding 向量）。"""

    document_id: int
    title: str
    content: str
    category: str
    keywords: List[str]
    status: str
    document_version: int
    knowledge_version: Optional[int]
    source_type: Optional[str]
    source_label: Optional[str]
    created_by: Optional[str]
    updated_by: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    published_at: Optional[str]
    archived_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "content": self.content,
            "category": self.category,
            "keywords": list(self.keywords or []),
            "status": self.status,
            "document_version": self.document_version,
            "knowledge_version": self.knowledge_version,
            "source_type": self.source_type,
            "source_label": self.source_label,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "published_at": self.published_at,
            "archived_at": self.archived_at,
        }


@dataclass(frozen=True)
class PublishResult:
    """发布操作结果（计划 3.1：含文档/知识/来源版本与状态）。"""

    document_id: int
    document_version: int
    knowledge_version: Optional[int]
    source_version: Optional[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "knowledge_version": self.knowledge_version,
            "source_version": self.source_version,
            "status": self.status,
        }


@dataclass(frozen=True)
class RefreshRecord:
    """索引刷新状态记录（F3）：目标知识版本、索引版本、状态与失败原因。"""

    status: str  # pending/building/succeeded/failed
    requested_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    target_knowledge_version: Optional[int] = None
    index_version: Optional[int] = None
    source_version: Optional[str] = None
    document_count: Optional[int] = None
    error: Optional[str] = None
    is_locked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "target_knowledge_version": self.target_knowledge_version,
            "index_version": self.index_version,
            "source_version": self.source_version,
            "document_count": self.document_count,
            "error": self.error,
            "is_locked": self.is_locked,
        }
