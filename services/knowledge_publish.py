"""Phase F F3：知识发布/刷新服务 —— 流水线、索引版本与失败回退。

发布语义（计划 2.2）：
- 仅从 status=published 文档构建正式索引；草稿不可正式检索。
- 发布时：先构建"已发布文档 + 本条草稿"的候选索引（不交换），
  构建成功才提交 DB 并原子交换快照；Embedding/构建失败 -> 标 failed、
  保留旧快照，旧已发布内容继续可查（失败回退）。
- knowledge_version（语料版本）持久化于 knowledge_meta，跨重启恢复；
  source_version = index-{N}（E6 快照版本），每个 source_version 对应完整快照。
- 单进程 asyncio 锁串行发布/刷新；多进程尚未完成（明确记录）。

刷新(refresh)：仅按当前 published 文档重建正式索引（用于归档/恢复/历史回填后
对账），不改变语料 knowledge_version。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.knowledge_contracts import (
    IndexBuildFailedError,
    InvalidKnowledgeInputError,
    InvalidStateTransitionError,
    KnowledgeNotFoundError,
    KnowledgeNotReadyError,
    KnowledgeStatus,
    PublishResult,
    RefreshRecord,
    validate_transition,
)

logger = logging.getLogger(__name__)

_KNOWLEDGE_VERSION_KEY = "knowledge_version"


def _ts(dt) -> str:
    return dt.isoformat()


class KnowledgePublishService:
    """发布/刷新服务（容器唯一 KnowledgeService 之上的一层编排）。"""

    def __init__(self, knowledge_service):
        self._kb = knowledge_service
        self._repo = knowledge_service.db
        self._lock = asyncio.Lock()
        self._record: RefreshRecord = RefreshRecord(status="idle")

    # ---------------- 状态查询 ----------------

    def current_knowledge_version(self) -> int:
        return int(self._repo.get_meta(_KNOWLEDGE_VERSION_KEY, 0) or 0)

    def refresh_status(self, requested: bool = False) -> Dict[str, Any]:
        sv = self.get_source_version()
        rec = self._record
        return {
            "status": rec.status,
            "knowledge_version": self.current_knowledge_version(),
            "source_version": sv,
            "requested_at": rec.requested_at,
            "started_at": rec.started_at,
            "finished_at": rec.finished_at,
            "target_knowledge_version": rec.target_knowledge_version,
            "index_version": rec.index_version,
            "document_count": rec.document_count,
            "error": rec.error,
            "is_locked": self._lock.locked(),
            "multi_process": False,
        }

    def get_source_version(self) -> str:
        try:
            return self._kb.get_source_version()
        except Exception:
            return "index-0"

    # ---------------- 发布 ----------------

    async def publish_document(self, doc_id: int) -> Dict[str, Any]:
        """发布单条草稿：构建候选 -> 提交 -> 原子交换；失败保留旧快照。"""
        async with self._lock:
            self._record = RefreshRecord(
                status="building", started_at=_ts(datetime.now(timezone.utc)),
            )
            doc = self._repo.get_document(int(doc_id))
            if not doc:
                raise KnowledgeNotFoundError(f"知识文档不存在: {doc_id}")
            current = KnowledgeStatus(doc["status"])
            if current != KnowledgeStatus.DRAFT:
                raise InvalidStateTransitionError(
                    f"仅草稿可发布，当前状态 {current.value}"
                )
            content = str(doc.get("content") or "").strip()
            if not content:
                raise InvalidKnowledgeInputError("正文为空，无法发布")

            # 1) 计算本条新嵌入（不落库，构建成功后再提交）
            from services.text_embedding import embed_input
            try:
                emb = embed_input(
                    f"{content} {' '.join(doc.get('keywords') or [])}"
                )
            except Exception as e:
                self._mark_failed(doc, f"Embedding 失败: {e}")
                raise IndexBuildFailedError(f"Embedding 失败: {e}")

            # 2) 构建候选 = 已发布文档 + 本条（视为已发布）；失败不交换
            published = self._repo.get_published_documents()
            by_id: Dict[int, Dict[str, Any]] = {d["id"]: d for d in published}
            target_row = dict(doc)
            target_row["embedding"] = emb
            by_id[int(doc_id)] = target_row
            try:
                candidate = self._kb._assemble_candidate(list(by_id.values()))
            except Exception as e:
                self._mark_failed(doc, f"索引构建失败: {e}")
                raise IndexBuildFailedError(f"索引构建失败: {e}")
            if candidate is None:
                self._mark_failed(doc, "没有可嵌入文档，索引为空")
                raise IndexBuildFailedError("没有可嵌入文档，索引为空")

            # 3) 提交 DB（成功后原子交换；若 DB 失败不交换，保留旧快照）
            new_kv = self.current_knowledge_version() + 1
            now = datetime.now(timezone.utc)
            old_doc = dict(doc)
            old_meta = self._repo.get_meta(_KNOWLEDGE_VERSION_KEY, None)
            old_snapshot = self._kb.snapshot_state()
            try:
                self._repo.update_document(
                    int(doc_id),
                    status=KnowledgeStatus.PUBLISHED.value,
                    document_version=int(doc.get("document_version") or 0) + 1,
                    knowledge_version=new_kv,
                    embedding=emb,
                    published_at=now,
                    updated_by="operator",
                )
                self._repo.set_meta(_KNOWLEDGE_VERSION_KEY, new_kv)
            except Exception as e:
                self._restore_publish_state(doc_id, old_doc, old_meta, old_snapshot)
                self._mark_failed(doc, f"数据库提交失败: {e}")
                raise IndexBuildFailedError(f"数据库提交失败: {e}")

            try:
                sv = self._kb.swap_candidate(candidate)
            except Exception as e:
                # DB 已提交但索引尚未切换：恢复两侧状态，避免 published 文档
                # 与旧索引快照不一致；恢复后仍将本次发布标记为 failed。
                self._restore_publish_state(doc_id, old_doc, old_meta, old_snapshot)
                self._mark_failed(doc, f"索引交换失败: {e}")
                raise IndexBuildFailedError(f"索引交换失败: {e}")
            self._record = RefreshRecord(
                status="succeeded",
                started_at=self._record.started_at,
                finished_at=_ts(datetime.now(timezone.utc)),
                target_knowledge_version=new_kv,
                index_version=self._kb._index_version,
                source_version=sv,
                document_count=len(candidate[1]),
            )
            return PublishResult(
                document_id=int(doc_id),
                document_version=int(doc.get("document_version") or 0) + 1,
                knowledge_version=new_kv,
                source_version=sv,
                status=KnowledgeStatus.PUBLISHED.value,
            ).to_dict()

    def _restore_publish_state(self, doc_id, old_doc, old_meta, old_snapshot) -> None:
        """尽力恢复发布前的数据库、语料版本和内存快照。"""
        try:
            self._repo.restore_document_state(int(doc_id), old_doc)
            self._repo.set_meta(_KNOWLEDGE_VERSION_KEY, old_meta)
            self._kb.restore_snapshot_state(old_snapshot)
        except Exception:
            logger.exception("发布回滚失败，当前状态需要人工核对")

    async def refresh(self) -> Dict[str, Any]:
        """重建当前正式索引（仅 published），用于归档/恢复后的对账。"""
        async with self._lock:
            self._record = RefreshRecord(
                status="building", started_at=_ts(datetime.now(timezone.utc)),
            )
            try:
                documents = self._repo.get_published_documents()
                if not documents:
                    with self._kb._lock:
                        self._kb._snapshot = None
                        self._kb.document_ids = []
                    sv = self.get_source_version()
                else:
                    candidate = self._kb._assemble_candidate(documents)
                    if candidate is None:
                        raise IndexBuildFailedError("没有可嵌入文档，索引为空")
                    sv = self._kb.swap_candidate(candidate)
                kv = self.current_knowledge_version()
                self._record = RefreshRecord(
                    status="succeeded",
                    started_at=self._record.started_at,
                    finished_at=_ts(datetime.now(timezone.utc)),
                    target_knowledge_version=kv,
                    index_version=self._kb._index_version,
                    source_version=sv,
                    document_count=len(documents),
                )
                return self.refresh_status()
            except Exception as e:
                self._record = RefreshRecord(
                    status="failed",
                    started_at=self._record.started_at,
                    finished_at=_ts(datetime.now(timezone.utc)),
                    error=str(e)[:200],
                )
                logger.error("知识索引刷新失败: %s", e)
                raise

    # ---------------- 内部 ----------------

    def _mark_failed(self, doc: Dict[str, Any], reason: str) -> None:
        """发布失败：把文档状态标为 failed（保留旧快照，旧版本继续可查）。"""
        try:
            self._repo.update_document(
                int(doc["id"]),
                status=KnowledgeStatus.FAILED.value,
                updated_by="operator",
            )
        except Exception:
            logger.exception("发布失败标记 failed 时出错")
        self._record = RefreshRecord(
            status="failed",
            started_at=self._record.started_at,
            finished_at=_ts(datetime.now(timezone.utc)),
            target_knowledge_version=self.current_knowledge_version(),
            error=reason[:200],
        )
