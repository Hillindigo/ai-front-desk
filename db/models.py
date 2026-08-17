from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

# ============================================================
# Phase B（B1）：会话与消息持久化
# ============================================================

class Conversation(Base):
    """用户会话（Phase B）。

    会话是消息与运行时状态的归属边界；id 为 UUID 字符串（服务端生成，
    客户端不可覆盖）。查询时须同时校验 user_id 归属。
    """
    __tablename__ = 'conversations'

    id = Column(String(36), primary_key=True)
    user_id = Column(String(64), nullable=False, default='default_user', index=True)
    channel = Column(String(32), nullable=False, default='web')
    status = Column(String(16), nullable=False, default='active')  # active/closed
    active_workflow = Column(String(32), nullable=True)            # Phase C 状态机接入前可为空
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_activity_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship(
        "Message", back_populates="conversation",
        cascade="all, delete-orphan", order_by="Message.sequence",
    )


class Message(Base):
    """会话内消息（Phase B）。

    content 保存可恢复原文；sequence 为会话内序号（由 Repository 按
    max(sequence)+1 生成，同一会话由锁串行写入，保证单调不重复）。
    """
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(36), ForeignKey('conversations.id'), nullable=False, index=True)
    role = Column(String(16), nullable=False)              # user/assistant/system/tool
    content = Column(Text, nullable=False)
    message_type = Column(String(16), nullable=False, default='text')  # text/status/error
    metadata_json = Column(Text, nullable=True)            # 可序列化附加信息（JSON 字符串）
    sequence = Column(Integer, nullable=False)             # 会话内有序序号
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Technician(Base):
    __tablename__ = 'technicians'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    gender = Column(String, nullable=True)      # 新增性别字段
    strength = Column(String, nullable=True)    # 新增力气/倾向性字段
    schedules = relationship("TechnicianSchedule", back_populates="technician", cascade="all, delete-orphan")

class TechnicianSchedule(Base):
    __tablename__ = 'technician_schedules'
    id = Column(Integer, primary_key=True)
    technician_id = Column(Integer, ForeignKey('technicians.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)  # 'busy' or 'free'
    appointment_id = Column(Integer, nullable=True)
    technician = relationship("Technician", back_populates="schedules")

class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

class UserBehavior(Base):
    __tablename__ = 'user_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')  # 单用户场景使用默认用户ID
    action_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'inquiry'
    action_data = Column(JSON, nullable=True)  # 存储行为相关的详细数据
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    technician = relationship("Technician")

class UserPreference(Base):
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    preference_type = Column(String, nullable=False)  # 'technician', 'time', 'service', 'duration'
    preference_value = Column(String, nullable=False)
    confidence_score = Column(Integer, default=1)  # 偏好的置信度（出现次数）
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserRecommendation(Base):
    __tablename__ = 'user_recommendations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    recommendation_type = Column(String, nullable=False)  # 'technician_available', 'return_reminder', 'service_suggestion'
    content = Column(Text, nullable=False)
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    is_sent = Column(Integer, default=0)  # 是否已发送
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    technician = relationship("Technician")
