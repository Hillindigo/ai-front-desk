"""Phase F F4：版本化知识管理 API（/api/v1/knowledge）。

计划 3.1 规范管理接口；沿用 IdentityResolver 演示身份边界：
- 请求体 user_id 只作兼容校验/归属字段，不能成为权限来源；
- 未完成真实管理员鉴权（本阶段明确记录，不宣称生产可用）。

错误码映射：
- INVALID_INPUT -> 422
- KNOWLEDGE_NOT_FOUND -> 404
- INVALID_STATE_TRANSITION / KNOWLEDGE_VERSION_CONFLICT -> 409
- KNOWLEDGE_NOT_READY -> 503
- INDEX_BUILD_FAILED / INTERNAL_ERROR -> 500

响应不返回 embedding、Prompt 或供应商原始响应的任何字段。
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.chat_handler import get_container
from application.identity import IdentityError
from services.knowledge_contracts import (
    IndexBuildFailedError,
    InvalidKnowledgeInputError,
    InvalidStateTransitionError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgeNotReadyError,
    KnowledgeVersionConflictError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识管理"])


# ---------------- 请求模型 ----------------


class DocumentCreate(BaseModel):
    title: str = ""
    content: str
    category: str
    keywords: List[str] = []
    source_label: Optional[str] = None
    user_id: str = "default_user"  # 兼容字段


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    keywords: Optional[List[str]] = None
    source_label: Optional[str] = None
    user_id: str = "default_user"


class SearchPreviewRequest(BaseModel):
    query: str
    top_k: int = 3
    category: Optional[str] = None
    include_draft_ids: Optional[List[int]] = None
    user_id: str = "default_user"


# ---------------- 辅助 ----------------

STATUS_CODE_BY_ERROR = {
    InvalidKnowledgeInputError: 422,
    KnowledgeNotFoundError: 404,
    InvalidStateTransitionError: 409,
    KnowledgeVersionConflictError: 409,
    KnowledgeNotReadyError: 503,
    IndexBuildFailedError: 500,
}


def _handle(exc: Exception):
    """把知识领域错误映射为 HTTP 异常；未知错误记录后按 INTERNAL_ERROR。"""
    if isinstance(exc, KnowledgeError):
        code = exc.code
        status = STATUS_CODE_BY_ERROR.get(type(exc), 500)
        return HTTPException(status_code=status, detail={"code": code, "message": exc.message})
    logger.exception("知识管理接口未捕获错误")
    return HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "服务内部错误"})


def _identity(user_id: str) -> str:
    try:
        return get_container().identity_resolver.resolve(user_id)
    except IdentityError:
        raise HTTPException(status_code=403, detail={
            "code": "ACCESS_DENIED", "message": "身份校验失败，归属不符",
        })


def _ser():
    c = get_container()
    return c.knowledge_management, c.knowledge_publish, c.knowledge_service


# ---------------- 文档 ----------------

@router.get("/documents")
def list_documents(status: Optional[str] = None, category: Optional[str] = None,
                   keyword: Optional[str] = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   user_id: str = "default_user"):
    """按状态/分类/关键词分页查询文档。"""
    _identity(user_id)
    try:
        mgmt, _, _ = _ser()
        return mgmt.list_documents(status=status, category=category, keyword=keyword,
                                   page=page, page_size=page_size)
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


@router.post("/documents", status_code=201)
def create_document(body: DocumentCreate):
    """创建草稿。"""
    identity = _identity(body.user_id)
    try:
        mgmt, _, _ = _ser()
        return mgmt.create_document(
            title=body.title, content=body.content, category=body.category,
            keywords=body.keywords, source_type="manual", source_label=body.source_label,
            created_by=identity,
        )
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, user_id: str = "default_user"):
    """查看文档与版本。"""
    _identity(user_id)
    try:
        mgmt, _, _ = _ser()
        return mgmt.get_document(doc_id)
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


@router.put("/documents/{doc_id}")
def update_document(doc_id: int, body: DocumentUpdate):
    """更新草稿或待发布版本；已发布文档编辑后降为草稿。"""
    identity = _identity(body.user_id)
    try:
        mgmt, _, _ = _ser()
        return mgmt.update_document(
            doc_id, title=body.title, content=body.content, category=body.category,
            keywords=body.keywords, source_label=body.source_label, updated_by=identity,
        )
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


@router.post("/documents/{doc_id}/preview")
async def preview_document(doc_id: int, body: Optional[SearchPreviewRequest] = None):
    """候选版本检索预览（仅该草稿 + 已发布；结果标记 preview:true）。"""
    _identity("default_user")
    query = (body.query if body else None) or ""
    try:
        _, _, kb = _ser()
        rows = await kb.search_candidate(
            query, top_k=(body.top_k if body else 3),
            category=(body.category if body else None),
            include_draft_ids=[doc_id],
        )
        return {"document_id": doc_id, "preview": True, "results": rows}
    except Exception as exc:
        raise _handle(exc)


@router.post("/documents/{doc_id}/publish")
async def publish_document(doc_id: int, user_id: str = "default_user"):
    """校验、构建并发布。"""
    _identity(user_id)
    try:
        _, pub, _ = _ser()
        return await pub.publish_document(doc_id)
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


@router.post("/documents/{doc_id}/archive")
async def archive_document(doc_id: int, user_id: str = "default_user"):
    """幂等归档。"""
    identity = _identity(user_id)
    try:
        mgmt, _, _ = _ser()
        return await mgmt.archive_document(doc_id, updated_by=identity)
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


# ---------------- 刷新 ----------------

@router.get("/refresh")
def get_refresh(user_id: str = "default_user"):
    """查询刷新状态。"""
    _identity(user_id)
    try:
        _, pub, _ = _ser()
        return pub.refresh_status()
    except Exception as exc:
        raise _handle(exc)


@router.post("/refresh")
async def rebuild_index(user_id: str = "default_user"):
    """重建当前发布索引（归档/恢复后对账，不改变语料知识版本）。"""
    _identity(user_id)
    try:
        _, pub, _ = _ser()
        return await pub.refresh()
    except KnowledgeError as exc:
        raise _handle(exc)
    except Exception as exc:
        raise _handle(exc)


# ---------------- 管理检索预览 ----------------

@router.post("/search/preview")
async def search_preview(body: SearchPreviewRequest):
    """管理检索预览：可在候选（已发布 + 指定草稿）上检索，结果标记 preview。"""
    _identity(body.user_id)
    try:
        _, _, kb = _ser()
        rows = await kb.search_candidate(
            body.query, top_k=body.top_k, category=body.category,
            include_draft_ids=body.include_draft_ids or [],
        )
        return {"preview": True, "results": rows}
    except Exception as exc:
        raise _handle(exc)
