# services/knowledge_service.py

import threading
import numpy as np
import faiss
from typing import List, Dict, Tuple, Optional
from db.db_router import DatabaseRouter
from .text_embedding import embed_input
import logging

logger = logging.getLogger(__name__)

class KnowledgeService:
    """知识库服务类 - 结合数据库存储和向量检索

    Phase E E6 最小可靠性封口：
    - 查询读取不可变索引快照（(index, doc_ids, version) 原子替换），不与重建交换半成品。
    - 结果必须超过配置化相似度阈值 min_score 才能返回（低于阈值 = 无可靠依据）。
    - 可选关键词预过滤缩减候选（不得绕过相似度阈值）。
    - source_version 随重建递增，旧结果不得被标记为当前证据。
    """

    def __init__(self, db_path: str | None = None,
                 min_score: float = 0.5,
                 max_candidates: int = 20,
                 enable_keyword_prefilter: bool = True):  # Phase B 决策一：None 时取 db_config
        # 使用统一的DatabaseRouter，符合架构设计
        self.db_router = DatabaseRouter(db_path)
        self.db = self.db_router.knowledge  # 通过router访问knowledge repository
        self.index = None
        self.document_ids = []  # 维护文档ID与索引位置的映射
        self.initialized = False

        # Phase E E6：阈值、候选边界、索引快照与并发锁
        self.min_score = min_score
        self.max_candidates = max_candidates
        self.enable_keyword_prefilter = enable_keyword_prefilter
        self._lock = threading.RLock()
        self._snapshot: Optional[Tuple] = None  # (index, doc_ids_tuple, version)
        self._index_version = 0
        
        # 默认知识库内容
        self.default_knowledge = [
            {
                "content": "我们的门店营业时间是每天上午9点到晚上10点，全年无休。具体节假日安排请以门店公告为准。",
                "category": "营业时间",
                "keywords": ["营业时间", "开门", "关门", "几点", "时间"]
            },
            {
                "content": "我们提供多种线下服务项目：基础护理（120元/60分钟）、肩颈放松（80元/30分钟）、足部护理（100元/45分钟）、深度放松（90元/40分钟）。实际价格和时长以门店配置为准。",
                "category": "服务项目",
                "keywords": ["服务", "项目", "价格", "收费", "多少钱", "时长"]
            },
            {
                "content": "我们有经过培训的服务人员为您提供服务。您可以根据项目需求、时间和个人偏好选择合适的服务人员。",
                "category": "服务人员信息",
                "keywords": ["服务人员", "人员", "男", "女", "专业", "培训"]
            },
            {
                "content": "门店地址、交通方式和停车信息由运营人员在知识库中维护，请以当前门店配置为准。",
                "category": "门店地址",
                "keywords": ["地址", "门店信息", "到达方式", "交通"]
            },
            {
                "content": "基础护理适合希望放松和恢复状态的客户。系统只提供门店服务信息，不替代医疗诊断或专业健康建议。",
                "category": "服务介绍",
                "keywords": ["基础护理", "效果", "作用", "好处", "适合"]
            },
            {
                "content": "肩颈放松适合长期伏案、希望舒缓肩颈紧张的客户。服务选择应以客户自身情况和门店专业人员建议为准。",
                "category": "服务介绍",
                "keywords": ["肩颈放松", "肩颈", "肩膀", "紧张", "久坐"]
            },
            {
                "content": "足部护理以舒适体验和日常放松为主，具体服务步骤、注意事项和适用范围由门店知识库维护。",
                "category": "服务介绍",
                "keywords": ["足部护理", "脚", "放松", "睡眠", "疲劳"]
            },
            {
                "content": "我们的服务人员定期接受培训，门店通过服务记录和客户反馈持续改进体验。具体资质和服务标准由运营人员维护。",
                "category": "服务质量",
                "keywords": ["经验", "专业", "培训", "质量", "舒适"]
            },
            {
                "content": "如需取消或更改预约，请提前至少2小时通知我们。临时取消可能会产生一定的费用。",
                "category": "预约政策",
                "keywords": ["取消", "更改", "改期", "退约", "政策"]
            },
            {
                "content": "我们提供会员卡服务，充值500元送50元，充值1000元送150元。会员还可享受预约优先权和生日优惠。",
                "category": "会员服务",
                "keywords": ["会员", "充值", "优惠", "折扣", "生日"]
            }
        ]

    async def initialize(self):
        """初始化知识库服务"""
        try:
            # 检查数据库中是否已有数据
            existing_docs = self.db.get_all_documents()
            
            if not existing_docs:
                logger.info("数据库为空，初始化默认知识库")
                await self._create_default_knowledge()
            else:
                logger.info(f"从数据库加载了 {len(existing_docs)} 条知识")
            
            # 构建向量索引
            await self._build_vector_index()
            self.initialized = True
            logger.info("知识库服务初始化完成")
            
        except Exception as e:
            logger.error(f"知识库服务初始化失败: {e}")
            raise

    async def _create_default_knowledge(self):
        """创建默认知识库"""
        for knowledge in self.default_knowledge:
            try:
                # 生成嵌入向量
                text_for_embedding = f"{knowledge['content']} {' '.join(knowledge['keywords'])}"
                embedding = embed_input(text_for_embedding)
                
                # 保存到数据库（Phase F F1：默认知识以 published 状态播种，带来源标识）
                self.db.add_document(
                    content=knowledge['content'],
                    category=knowledge['category'],
                    keywords=knowledge['keywords'],
                    embedding=embedding,
                    title=knowledge.get('title', knowledge['category']),
                    status="published",
                    source_type="system_default",
                    source_label="系统默认知识",
                    created_by="system",
                    document_version=1,
                )
                logger.debug(f"添加默认知识: {knowledge['content'][:50]}...")
                
            except Exception as e:
                logger.error(f"添加默认知识失败: {e}")

    async def _build_vector_index(self):
        """构建向量索引（E6 快照原子替换；F1：只从 published 文档构建，草稿不入正式检索）"""
        try:
            documents = self.db.get_published_documents()
            if not documents:
                logger.warning("没有已发布文档可用于构建索引")
                with self._lock:
                    self._snapshot = None
                    self.document_ids = []
                return

            embeddings = []
            doc_ids = []

            for doc in documents:
                if doc.get('embedding'):
                    embeddings.append(doc['embedding'])
                    doc_ids.append(doc['id'])
                else:
                    # 如果没有嵌入向量，生成一个
                    logger.warning(f"文档 {doc['id']} 缺少嵌入向量，正在生成...")
                    text_for_embedding = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
                    embedding = embed_input(text_for_embedding)

                    # 更新数据库
                    self.db.update_document(doc['id'], embedding=embedding)

                    embeddings.append(embedding)
                    doc_ids.append(doc['id'])

            if embeddings:
                # 创建FAISS索引
                embeddings_array = np.array(embeddings).astype('float32')
                dimension = embeddings_array.shape[1]
                index = faiss.IndexFlatIP(dimension)  # 内积相似度
                index.add(embeddings_array)

                # 原子替换快照（查询要么读旧快照，要么读新快照，绝不读中间状态）
                with self._lock:
                    self._index_version += 1
                    self.index = index
                    self.document_ids = doc_ids
                    self._snapshot = (index, tuple(doc_ids), self._index_version)
                logger.info(
                    f"构建向量索引完成，包含 {len(embeddings)} 个向量，version={self._index_version}"
                )
            else:
                logger.warning("没有有效的嵌入向量，无法构建索引")

        except Exception as e:
            logger.error(f"构建向量索引失败: {e}")
            # E6：失败时保留旧快照（若有），不破坏正在服务的查询
            raise

    async def search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        """搜索相关文档（E6：阈值过滤 + 候选边界；低于阈值的结果不返回）"""
        if not self.initialized:
            logger.warning("知识库服务未初始化或索引不可用")
            return []

        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            logger.warning("索引快照不可用（初始化为空或构建失败）")
            return []

        index, doc_ids, version = snapshot
        try:
            # 生成查询的嵌入向量
            query_embedding = embed_input(query)
            query_array = np.array([query_embedding]).astype('float32')

            # 关键词预过滤候选（只缩减候选集，不绕过相似度阈值）
            candidate_ids = None
            if self.enable_keyword_prefilter:
                candidate_ids = self._keyword_candidates(query, doc_ids)

            if candidate_ids is not None:
                # 预过滤启用：必须全量评估，确保候选文档不被 top-k 截断漏检
                k = len(doc_ids)
            else:
                k = min(max(top_k * 2, 1), len(doc_ids), self.max_candidates)
            scores, indices = index.search(query_array, k)

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(doc_ids):
                    continue
                doc_id = doc_ids[idx]
                if candidate_ids is not None and doc_id not in candidate_ids:
                    continue  # 预过滤候选之外不检索
                if float(score) < self.min_score:
                    continue  # 低于阈值 = 无可靠依据，不能进入回答上下文
                doc = self.db.get_document(doc_id)
                if not doc:
                    continue
                # F1：双保险——索引之外再校验当前状态必须是 published（归档/草稿不作为正式依据）
                if str(doc.get("status", "published")) != "published":
                    continue
                if category and doc.get('category') != category:
                    continue
                doc['score'] = float(score)
                doc['rank'] = len(results) + 1
                doc['source_version'] = f"index-{version}"
                results.append(doc)
                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.error(f"搜索知识库失败: {e}")
            return []

    def _keyword_candidates(self, query: str, doc_ids) -> Optional[set]:
        """关键词预过滤：query 命中文档 keywords 的作为优先候选；无命中回退 None(全部)。"""
        hits = set()
        for doc_id in doc_ids:
            doc = self.db.get_document(doc_id)
            if not doc:
                continue
            keywords = doc.get("keywords") or []
            if any(str(kw) in query for kw in keywords):
                hits.add(doc_id)
        return hits or None

    async def search_structured(self, query: str, top_k: int = 3,
                                category: str = None) -> List[Dict]:
        """E6：结构化检索结果（EvidenceReader 输入：文档ID/片段/分数/索引版本/排名）。"""
        rows = await self.search(query, top_k=top_k, category=category)
        return [
            {
                "document_id": int(r["id"]),
                "category": r.get("category", ""),
                "snippet": str(r.get("content", ""))[:200],
                "score": float(r.get("score", 0.0)),
                "source_version": r.get("source_version", "index-0"),
                "rank": int(r.get("rank", i + 1)),
            }
            for i, r in enumerate(rows)
        ]

    async def add_document(self, content: str, category: str, keywords: List[str] = None,
                           title: str = None, status: str = "draft",
                           source_type: str = None, source_label: str = None,
                           created_by: str = None) -> bool:
        """添加新文档（Phase F F2：支持治理字段；默认草稿，不自动上线）"""
        try:
            if keywords is None:
                keywords = []
            
            # 生成嵌入向量
            text_for_embedding = f"{content} {' '.join(keywords)}"
            embedding = embed_input(text_for_embedding)
            
            # 保存到数据库
            doc_id = self.db.add_document(
                content, category, keywords, embedding,
                title=title, status=status, source_type=source_type,
                source_label=source_label, created_by=created_by,
            )
            
            # 重建索引
            await self._build_vector_index()
            
            logger.info(f"成功添加文档 {doc_id}: {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    async def update_document(self, doc_id: int, content: str = None, category: str = None,
                              keywords: List[str] = None, title: str = None,
                              status: str = None, source_type: str = None,
                              source_label: str = None, updated_by: str = None) -> bool:
        """更新文档"""
        try:
            # 如果更新了内容或关键词，需要重新生成嵌入向量
            embedding = None
            if content is not None or keywords is not None:
                # 获取当前文档信息
                current_doc = self.db.get_document(doc_id)
                if not current_doc:
                    return False
                
                # 使用新值或保持原值
                final_content = content if content is not None else current_doc['content']
                final_keywords = keywords if keywords is not None else current_doc.get('keywords', [])
                
                # 生成新的嵌入向量
                text_for_embedding = f"{final_content} {' '.join(final_keywords)}"
                embedding = embed_input(text_for_embedding)
            
            # 更新数据库
            success = self.db.update_document(
                doc_id, content, category, keywords, embedding,
                title=title, status=status, source_type=source_type,
                source_label=source_label, updated_by=updated_by,
            )
            
            if success and embedding is not None:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"更新文档失败: {e}")
            return False

    async def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档"""
        try:
            success = self.db.delete_document(doc_id, soft_delete)
            
            if success:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict]:
        """获取所有文档"""
        return self.db.get_all_documents(include_inactive)

    def get_document(self, doc_id: int) -> Dict:
        """获取指定文档"""
        return self.db.get_document(doc_id)

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return self.db.get_all_categories()

    def get_documents_count(self) -> int:
        """获取文档总数"""
        return self.db.get_documents_count()

    def search_by_category(self, category: str) -> List[Dict]:
        """按分类搜索文档"""
        return self.db.search_documents_by_category(category)

    def search_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """按关键词搜索文档"""
        return self.db.search_documents_by_keywords(keywords)
