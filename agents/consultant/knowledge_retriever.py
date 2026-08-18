"""
知识检索器

负责从知识库中检索相关信息
"""

from typing import List, Dict, Any, Optional
import logging
from services.knowledge_service import KnowledgeService
logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """知识检索器"""
    
    def __init__(self, knowledge_service: Optional[KnowledgeService] = None):
        # 默认路径仍保持兼容；应用容器路径注入唯一实例，避免旧咨询链
        # 绕过容器重新创建 KnowledgeService。
        self.knowledge_service = knowledge_service or KnowledgeService()
        self.kb_initialized = False
    
    async def initialize(self):
        """初始化知识库服务"""
        if not self.kb_initialized:
            await self.knowledge_service.initialize()
            self.kb_initialized = True
            logger.info("✅ 咨询机器人知识库服务已初始化")
    
    async def search_knowledge(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """搜索相关知识"""
        # 确保知识库已初始化
        if not self.kb_initialized:
            await self.initialize()
        
        # 搜索相关知识
        relevant_docs = await self.knowledge_service.search(query, top_k=top_k)
        
        # 记录检索日志
        self._log_search_results(query, relevant_docs)
        
        return relevant_docs or []
    
    def _log_search_results(self, query: str, relevant_docs: List[Dict[str, Any]]):
        """记录搜索结果日志"""
        if relevant_docs:
            logger.info(f"🔍 知识库检索结果 (查询: '{query}'):")
            for i, doc in enumerate(relevant_docs, 1):
                score = doc.get('score', 0)
                category = doc.get('category', '未知')
                content = doc.get('content', '')[:80]
                logger.info(f"  {i}. [相关度:{score:.3f}] [分类:{category}] {content}...")
            logger.info(f"📊 知识库统计: 共检索到 {len(relevant_docs)} 条相关知识")
        else:
            logger.warning(f"⚠️ 知识库检索: 未找到与 '{query}' 相关的知识")
