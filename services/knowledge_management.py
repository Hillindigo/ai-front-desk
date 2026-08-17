"""Phase F F2：知识管理应用服务。

收敛文档的列表、详情、创建、更新、归档、版本查询与发布准备，作为唯一的管理入口；
底层复用容器持有的单一 `KnowledgeService`（其内部持有仓储与 FAISS 索引），
因此管理操作与咨询链路（KnowledgeEvidenceReader）读取同一索引实例。

发布流水线（真正构建候选索引并原子切换）在 F3 的发布/刷新服务中实现；
本服务只做发布准备校验与归档的确定性状态迁移 + 索引重建。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.knowledge_contracts import (
    InvalidKnowledgeInputError,
    KnowledgeDocumentContract,
    KnowledgeNotFoundError,
    KnowledgeStatus,
    validate_transition,
)

logger = logging.getLogger(__name__)


def _fmt(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


class KnowledgeManagementService:
    """知识管理服务（只操作容器持有的 KnowledgeService 及其仓储）。"""

    def __init__(self, knowledge_service):
        self._kb = knowledge_service
        self._repo = knowledge_service.db

    # ---------------- 查询 ----------------

    def list_documents(self, status: Optional[str] = None, category: Optional[str] = None,
                       keyword: Optional[str] = None, page: int = 1,
                       page_size: int = 20) -> Dict[str, Any]:
        if status and status not in {s.value for s in KnowledgeStatus}:
            raise InvalidKnowledgeInputError(f"未知状态: {status}")
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        rows = self._repo.list_documents(status=status, category=category, keyword=keyword)
        total = len(rows)
        start = (page - 1) * page_size
        items = [self._to_contract(r).to_dict() for r in rows[start:start + page_size]]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def get_document(self, doc_id: int) -> Dict[str, Any]:
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        return self._to_contract(doc).to_dict()

    def get_version(self, doc_id: int) -> Dict[str, Any]:
        """版本查询：返回文档版本信息与可发布状态（不返回正文全文）。"""
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        return {
            "document_id": int(doc["id"]),
            "document_version": int(doc.get("document_version") or 0),
            "knowledge_version": doc.get("knowledge_version"),
            "source_version": doc.get("knowledge_version"),
            "status": doc.get("status"),
            "updated_at": _fmt(doc.get("updated_at")),
            "published_at": _fmt(doc.get("published_at")),
        }

    # ---------------- 写操作 ----------------

    def create_document(self, *, title: str, content: str, category: str,
                        keywords: Optional[List[str]] = None,
                        source_type: str = "manual", source_label: Optional[str] = None,
                        created_by: Optional[str] = "operator") -> Dict[str, Any]:
        if not content or not content.strip():
            raise InvalidKnowledgeInputError("内容不能为空")
        if not category or not category.strip():
            raise InvalidKnowledgeInputError("分类不能为空")
        title = (title or "").strip() or category
        doc_id = self._repo.add_document(
            content=content, category=category, keywords=list(keywords or []),
            title=title, status=KnowledgeStatus.DRAFT.value,
            source_type=source_type, source_label=source_label,
            created_by=created_by, document_version=1,
        )
        return self.get_document(doc_id)

    def update_document(self, doc_id, *, title=None, content=None, category=None,
                        keywords=None, source_type=None, source_label=None,
                        updated_by: Optional[str] = "operator") -> Dict[str, Any]:
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        current = KnowledgeStatus(doc["status"])
        # 发布态直接编辑：先降为草稿（编辑副本），不再作为正式依据直到重新发布。
        if current == KnowledgeStatus.PUBLISHED:
            self._repo.update_document(int(doc_id), status=KnowledgeStatus.DRAFT.value)
            current = KnowledgeStatus.DRAFT
        elif current == KnowledgeStatus.ARCHIVED and content is not None:
            raise InvalidKnowledgeInputError("归档文档不可编辑正文（可用恢复后编辑）")
        if content is not None and not content.strip():
            raise InvalidKnowledgeInputError("内容不能为空")

        kwargs: Dict[str, Any] = {"updated_by": updated_by}
        if title is not None:
            kwargs["title"] = title.strip() or doc.get("title")
        if content is not None:
            kwargs["content"] = content
        if category is not None:
            kwargs["category"] = category
        if keywords is not None:
            kwargs["keywords"] = list(keywords)
        if source_type is not None:
            kwargs["source_type"] = source_type
        if source_label is not None:
            kwargs["source_label"] = source_label
        self._repo.update_document(int(doc_id), **kwargs)
        return self.get_document(int(doc_id))

    async def archive_document(self, doc_id, updated_by: Optional[str] = "operator") -> Dict[str, Any]:
        """归档：published/draft -> archived，并从正式索引移除；幂等。"""
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        current = KnowledgeStatus(doc["status"])
        if current == KnowledgeStatus.ARCHIVED:
            # 幂等归档：已归档直接返回
            return self.get_document(int(doc_id))
        validate_transition(current, KnowledgeStatus.ARCHIVED)
        self._repo.update_document(
            int(doc_id), status=KnowledgeStatus.ARCHIVED.value,
            archived_at=datetime.now(timezone.utc), updated_by=updated_by,
        )
        await self._kb._build_vector_index()  # 归档项移出正式索引
        return self.get_document(int(doc_id))

    def publish_prepare(self, doc_id: int) -> Dict[str, Any]:
        """发布准备校验（F2）：仅校验可发布性，不构建索引（构建在 F3）。"""
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        current = KnowledgeStatus(doc["status"])
        can_publish = current == KnowledgeStatus.DRAFT and bool((doc.get("content") or "").strip())
        reasons: List[str] = []
        if current != KnowledgeStatus.DRAFT:
            reasons.append(f"当前状态 {current.value} 不可发布，需为草稿")
        if not (doc.get("content") or "").strip():
            reasons.append("正文为空")
        return {
            "document_id": int(doc["id"]),
            "can_publish": can_publish and not reasons,
            "reasons": reasons,
            "document_version": int(doc.get("document_version") or 0),
            "status": doc.get("status"),
        }

    def restore_document(self, doc_id: int, updated_by: Optional[str] = "operator") -> Dict[str, Any]:
        """恢复：archived -> draft（可再编辑/发布后正式上线）。"""
        doc = self._repo.get_document(int(doc_id))
        if not doc:
            raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
        current = KnowledgeStatus(doc["status"])
        validate_transition(current, KnowledgeStatus.DRAFT)
        self._repo.update_document(
            int(doc_id), status=KnowledgeStatus.DRAFT.value,
            updated_by=updated_by,
        )
        return self.get_document(int(doc_id))

    # ---------------- 内部 ----------------

    def _to_contract(self, doc: Dict[str, Any]) -> KnowledgeDocumentContract:
        return KnowledgeDocumentContract(
            document_id=int(doc["id"]),
            title=str(doc.get("title") or ""),
            content=str(doc.get("content") or ""),
            category=str(doc.get("category") or ""),
            keywords=list(doc.get("keywords") or []),
            status=str(doc.get("status") or KnowledgeStatus.DRAFT.value),
            document_version=int(doc.get("document_version") or 0),
            knowledge_version=doc.get("knowledge_version"),
            source_type=doc.get("source_type"),
            source_label=doc.get("source_label"),
            created_by=doc.get("created_by"),
            updated_by=doc.get("updated_by"),
            created_at=_fmt(doc.get("created_at")),
            updated_at=_fmt(doc.get("updated_at")),
            published_at=_fmt(doc.get("published_at")),
            archived_at=_fmt(doc.get("archived_at")),
        )
