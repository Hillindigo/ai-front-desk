from typing import List, Dict, Any, Optional
from datetime import datetime
from ..base.interfaces import BaseKnowledgeRepository
from ..base.session_manager import SessionManager
from ..models import KnowledgeDocument


class KnowledgeRepository(BaseKnowledgeRepository):
    """
    知识库数据访问对象
    
    职责：
    1. 知识文档的CRUD操作
    2. 文档搜索和分类
    3. 文档状态管理
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化知识库数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None, 
                    embedding: Optional[List[float]] = None,
                    title: Optional[str] = None,
                    status: Optional[str] = "draft",
                    source_type: Optional[str] = None,
                    source_label: Optional[str] = None,
                    created_by: Optional[str] = None,
                    document_version: int = 1) -> int:
        """
        添加知识文档
        
        Args:
            content: 文档内容
            category: 文档分类
            keywords: 关键词列表
            embedding: 嵌入向量
            title: 文档标题（Phase F）
            status: 文档状态 draft/published/archived/failed（Phase F）
            source_type: 来源类型（Phase F）
            source_label: 来源可读标签（Phase F）
            created_by: 创建人（Phase F）
            document_version: 文档版本（Phase F）
            
        Returns:
            新创建的文档ID
        """
        with self.session_manager.session_scope() as session:
            document = KnowledgeDocument(
                content=content,
                category=category,
                keywords=keywords,
                embedding=embedding,
                title=title,
                status=status,
                source_type=source_type,
                source_label=source_label,
                created_by=created_by,
                document_version=document_version,
            )
            session.add(document)
            session.flush()
            return document.id

    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """
        获取指定文档
        
        Args:
            doc_id: 文档ID
            
        Returns:
            文档信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return None
                
            return self._document_to_dict(document)

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """
        获取所有文档
        
        Args:
            include_inactive: 是否包含已删除的文档
            
        Returns:
            文档信息列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(KnowledgeDocument)
            
            if not include_inactive:
                query = query.filter(KnowledgeDocument.is_active == 1)
                
            documents = query.all()
            return [self._document_to_dict(doc) for doc in documents]

    def get_published_documents(self) -> List[Dict[str, Any]]:
        """获取已发布且未删除的文档（Phase F：正式检索索引只由此构建）。"""
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1,
                KnowledgeDocument.status == "published",
            ).all()
            return [self._document_to_dict(doc) for doc in documents]

    def list_documents(self, status: Optional[str] = None,
                       category: Optional[str] = None,
                       keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """按状态/分类/关键词过滤列出文档（Phase F F2：分页由服务层做）。

        keyword 匹配标题或正文（包含匹配，大小写不敏感）。
        """
        with self.session_manager.session_scope() as session:
            query = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            )
            if status:
                query = query.filter(KnowledgeDocument.status == status)
            if category:
                query = query.filter(KnowledgeDocument.category == category)
            documents = query.all()
            rows = [self._document_to_dict(doc) for doc in documents]
        if keyword:
            kw = keyword.strip().lower()
            rows = [
                r for r in rows
                if kw in str(r.get("title") or "").lower()
                or kw in str(r.get("content") or "").lower()
            ]
        return rows

    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None, 
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None,
                       title: Optional[str] = None, status: Optional[str] = None,
                       source_type: Optional[str] = None, source_label: Optional[str] = None,
                       updated_by: Optional[str] = None, document_version: Optional[int] = None,
                       knowledge_version: Optional[int] = None,
                       published_at: Optional[Any] = None, archived_at: Optional[Any] = None) -> bool:
        """
        更新文档（Phase F：支持标题/状态/来源/版本/发布时间等治理字段）
        
        Args:
            doc_id: 文档ID
            content: 新内容
            category: 新分类
            keywords: 新关键词
            embedding: 新嵌入向量
            title: 新标题
            status: 新状态
            source_type: 来源类型
            source_label: 来源可读标签
            updated_by: 更新人
            document_version: 文档版本
            knowledge_version: 知识版本
            published_at: 发布时间
            archived_at: 归档时间
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            if content is not None:
                document.content = content
            if category is not None:
                document.category = category
            if keywords is not None:
                document.keywords = keywords
            if embedding is not None:
                document.embedding = embedding
            if title is not None:
                document.title = title
            if status is not None:
                document.status = status
            if source_type is not None:
                document.source_type = source_type
            if source_label is not None:
                document.source_label = source_label
            if updated_by is not None:
                document.updated_by = updated_by
            if document_version is not None:
                document.document_version = document_version
            if knowledge_version is not None:
                document.knowledge_version = knowledge_version
            if published_at is not None:
                document.published_at = published_at
            if archived_at is not None:
                document.archived_at = archived_at
            
            document.updated_at = datetime.utcnow()
            return True

    def restore_document_state(self, doc_id: int, state: Dict[str, Any]) -> bool:
        """恢复发布前的完整文档状态，允许显式恢复 NULL 字段。"""
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            if not document:
                return False
            for field in (
                "title", "content", "category", "keywords", "embedding", "status",
                "document_version", "knowledge_version", "source_type", "source_label",
                "created_by", "updated_by", "published_at", "archived_at", "is_active",
            ):
                if field in state:
                    setattr(document, field, state[field])
            document.updated_at = datetime.utcnow()
            return True

    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """
        删除文档（支持软删除）
        
        Args:
            doc_id: 文档ID
            soft_delete: 是否软删除
            
        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            
            if soft_delete:
                document.is_active = 0
                document.updated_at = datetime.utcnow()
            else:
                session.delete(document)
            
            return True

    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        按分类搜索文档
        
        Args:
            category: 文档分类
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.category == category,
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        按关键词搜索文档
        
        Args:
            keywords: 关键词列表
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).all()
            
            # 简单的关键词匹配
            matched_docs = []
            for doc in documents:
                doc_keywords = doc.keywords or []
                if any(keyword in doc_keywords for keyword in keywords):
                    matched_docs.append(self._document_to_dict(doc))
            
            return matched_docs

    def search_documents_by_content(self, search_text: str) -> List[Dict[str, Any]]:
        """
        按内容搜索文档
        
        Args:
            search_text: 搜索文本
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.content.contains(search_text),
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def get_all_categories(self) -> List[str]:
        """
        获取所有分类
        
        Returns:
            分类列表
        """
        with self.session_manager.session_scope() as session:
            categories = session.query(KnowledgeDocument.category).filter(
                KnowledgeDocument.is_active == 1
            ).distinct().all()
            
            return [cat[0] for cat in categories]

    def get_documents_count(self) -> int:
        """
        获取文档总数
        
        Returns:
            活跃文档数量
        """
        with self.session_manager.session_scope() as session:
            return session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).count()

    def get_documents_by_category_count(self) -> Dict[str, int]:
        """
        获取各分类的文档数量
        
        Returns:
            分类和文档数量的字典
        """
        with self.session_manager.session_scope() as session:
            from sqlalchemy import func
            
            result = session.query(
                KnowledgeDocument.category,
                func.count(KnowledgeDocument.id).label('count')
            ).filter(
                KnowledgeDocument.is_active == 1
            ).group_by(KnowledgeDocument.category).all()
            
            return {category: count for category, count in result}

    # ---------------- 语料元信息（Phase F F3） ----------------

    def get_meta(self, key: str, default=None):
        """读取知识语料元信息键值。"""
        from ..models import KnowledgeMeta
        with self.session_manager.session_scope() as session:
            row = session.query(KnowledgeMeta).filter(
                KnowledgeMeta.key == key
            ).first()
            return row.value if row else default

    def set_meta(self, key: str, value) -> None:
        """写入/更新知识语料元信息键值。"""
        from ..models import KnowledgeMeta, datetime as _dt
        with self.session_manager.session_scope() as session:
            row = session.query(KnowledgeMeta).filter(
                KnowledgeMeta.key == key
            ).first()
            if row is None:
                session.add(KnowledgeMeta(key=key, value=value))
            else:
                row.value = value
                row.updated_at = _dt.utcnow()

    def _document_to_dict(self, document: KnowledgeDocument) -> Dict[str, Any]:
        """将文档对象转换为字典（Phase F：含治理字段，不含敏感/内部嵌入外的公开字段）"""
        return {
            'id': document.id,
            'title': document.title,
            'content': document.content,
            'category': document.category,
            'keywords': document.keywords,
            'embedding': document.embedding,
            'status': document.status,
            'document_version': document.document_version,
            'knowledge_version': document.knowledge_version,
            'source_type': document.source_type,
            'source_label': document.source_label,
            'created_by': document.created_by,
            'updated_by': document.updated_by,
            'created_at': document.created_at,
            'updated_at': document.updated_at,
            'published_at': document.published_at,
            'archived_at': document.archived_at,
            'is_active': bool(document.is_active)
        }
