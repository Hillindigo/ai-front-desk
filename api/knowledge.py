"""
知识库管理API（旧 /api/knowledge 薄适配，Phase F F2）

历史遗留接口。Phase F F2 起不再路由内直接构造 KnowledgeService，也不依赖
未定义的应用对象；统一从应用容器（api.chat_handler.get_container）取单一
KnowledgeService 实例，与管理 API / 咨询链路共享同一索引。
正式版本化知识 API 见 /api/v1/knowledge（Phase F F4）。

本接口保留旧契约（question+answer 拼接 content），仅做薄转发；迁移/删除条件
记录于 Phase F F9 交接。
"""
from fastapi import APIRouter, HTTPException
import logging
from typing import List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["知识库管理"])


async def _shared_service():
    """从应用容器取单一 KnowledgeService（不存在则惰性构建，确保已初始化）。"""
    from api.chat_handler import get_container
    container = get_container()
    await container.initialize()
    return container.knowledge_service


class KnowledgeItem(BaseModel):
    id: int = None
    question: str
    answer: str
    category: str = "general"


class SearchRequest(BaseModel):
    query: str


@router.get("/")
async def get_all_knowledge():
    """获取所有知识条目"""
    try:
        knowledge_service = await _shared_service()
        entries = knowledge_service.get_all_documents()

        # 安全获取categories，避免出错
        try:
            categories = knowledge_service.get_all_categories()
        except Exception as e:
            logger.error(f"获取categories失败: {e}")
            categories = []

        return {
            "documents": entries or [],
            "categories": categories or [],
            "total_count": len(entries) if entries else 0,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识库失败: {str(e)}")


@router.get("/{knowledge_id}")
async def get_knowledge(knowledge_id: int):
    """获取特定知识条目"""
    try:
        knowledge_service = await _shared_service()
        entry = knowledge_service.get_document(knowledge_id)
        if not entry:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "data": entry}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识条目失败: {str(e)}")


@router.post("/")
async def add_knowledge(item: KnowledgeItem):
    """添加新的知识条目（F2：改用共享实例；保存为草稿，不自动上线）"""
    try:
        knowledge_service = await _shared_service()
        # 将问答组合成文档内容
        content = f"问题: {item.question}\n答案: {item.answer}"
        result = await knowledge_service.add_document(
            content=content,
            category=item.category,
            title=item.question,
            source_type="legacy_api",
            created_by="operator",
        )
        return {"status": "success", "message": "知识条目添加成功", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加知识条目失败: {str(e)}")


@router.put("/{knowledge_id}")
async def update_knowledge(knowledge_id: int, item: KnowledgeItem):
    """更新知识条目"""
    try:
        knowledge_service = await _shared_service()
        # 将问答组合成文档内容
        content = f"问题: {item.question}\n答案: {item.answer}"
        result = await knowledge_service.update_document(
            doc_id=knowledge_id,
            content=content,
            category=item.category,
            title=item.question,
        )
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "message": "知识条目更新成功", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新知识条目失败: {str(e)}")


@router.delete("/{knowledge_id}")
async def delete_knowledge(knowledge_id: int):
    """删除知识条目"""
    try:
        knowledge_service = await _shared_service()
        result = await knowledge_service.delete_document(knowledge_id)
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "message": "知识条目删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除知识条目失败: {str(e)}")


@router.post("/search")
async def search_knowledge(request: SearchRequest):
    """搜索知识库"""
    try:
        knowledge_service = await _shared_service()
        results = await knowledge_service.search(request.query)
        return {"status": "success", "data": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索知识库失败: {str(e)}")
