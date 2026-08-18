from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy import text
from datetime import datetime

Base = declarative_base()

# ============================================================
# Phase C（C1）：预约与排班领域
# ============================================================

class Appointment(Base):
    """预约实体（Phase C）。

    预约是"占用服务人员时间"的唯一业务来源；状态迁移由确定性领域服务控制。
    id 为 UUID 字符串；idempotency_key 按 (user_id, idempotency_key) 唯一。
    同一 conversation_id 最多一个活跃草稿（部分唯一索引，SQLite）。
    """
    __tablename__ = 'appointments'
    __table_args__ = (
        # 幂等键唯一约束（可空列：NULL 不参与唯一性，draft 无需幂等键）
        Index('ix_appointments_user_idempotency', 'user_id', 'idempotency_key', unique=True),
        # 冲突查询索引：按服务人员 + 时间
        Index('ix_appointments_technician_time', 'technician_id', 'start_time', 'end_time'),
        # 每会话最多一个活跃草稿（draft/pending_confirmation）
        Index(
            'uq_conversation_active_draft',
            'conversation_id',
            unique=True,
            sqlite_where=text(
                "conversation_id IS NOT NULL AND status IN ('draft', 'pending_confirmation')"
            ),
        ),
    )

    id = Column(String(36), primary_key=True)                      # UUID，服务端生成
    user_id = Column(String(64), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    conversation_id = Column(String(36), nullable=True, index=True)  # 可空：后台创建
    service_type = Column(String(64), nullable=False)              # 对外服务项目字段
    project = Column(String(64), nullable=True)                    # 迁移期兼容字段
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    start_time = Column(DateTime, nullable=True)                   # 草稿阶段可为空
    end_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    status = Column(String(24), nullable=False, default='draft', index=True)
    idempotency_key = Column(String(64), nullable=True)
    version = Column(Integer, nullable=False, default=1)           # 乐观版本号
    expires_at = Column(DateTime, nullable=True)                   # 草稿/待确认 TTL
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(String(128), nullable=True)

    technician = relationship("Technician")
    events = relationship(
        "AppointmentEvent", back_populates="appointment",
        cascade="all, delete-orphan", order_by="AppointmentEvent.id",
    )


class AppointmentEvent(Base):
    """预约事件追踪（Phase C）。

    与 Appointment 主记录同一事务写入；仅做审计与追踪，不作为当前状态唯一来源。
    """
    __tablename__ = 'appointment_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    appointment_id = Column(String(36), ForeignKey('appointments.id'), nullable=False, index=True)
    event_type = Column(String(24), nullable=False)  # created/confirmed/cancelled/expired/rescheduled/failed
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    request_id = Column(String(64), nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    appointment = relationship("Appointment", back_populates="events")


class Conversation(Base):
    """用户会话（Phase B）。

    会话是消息与运行时状态的归属边界；id 为 UUID 字符串（服务端生成，
    客户端不可覆盖）。查询时须同时校验 user_id 归属。
    """
    __tablename__ = 'conversations'

    id = Column(String(36), primary_key=True)
    user_id = Column(String(64), nullable=False, default='default_user', index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
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
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
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
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    title = Column(String, nullable=True)                    # 文档标题（Phase F F1）
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    status = Column(String, nullable=False, default='draft')  # draft/published/archived/failed
    document_version = Column(Integer, nullable=False, default=1)  # 单文档版本（发布递增）
    knowledge_version = Column(Integer, nullable=True)        # 发布时所在语料版本
    source_type = Column(String, nullable=True)               # 来源类型 e.g. legacy/system_default/manual
    source_label = Column(String, nullable=True)              # 来源可读标签
    created_by = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记


class KnowledgeMeta(Base):
    """知识语料元信息（Phase F F3）：跨重启持久化语料版本等键值。"""
    __tablename__ = 'knowledge_meta'
    key = Column(String, primary_key=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================
# Phase G（G1）：商家身份、门店成员与服务端会话
# ============================================================

class MerchantAccount(Base):
    """商家运营账号，与客户 user_id 完全分离。"""

    __tablename__ = "merchant_accounts"
    __table_args__ = (
        Index("uq_merchant_accounts_username", "username", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), nullable=False)
    password_hash = Column(String(512), nullable=False)
    display_name = Column(String(128), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Store(Base):
    """商家门店实体；G2 将业务事实逐步回填到该边界。"""

    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoreProfile(Base):
    """门店运营资料（G3）。"""

    __tablename__ = "store_profiles"
    __table_args__ = (Index("uq_store_profiles_store", "store_id", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    address = Column(String(256), nullable=True)
    phone = Column(String(64), nullable=True)
    is_open = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ServiceCatalogItem(Base):
    """门店可预约服务目录；金额使用分，避免浮点误差。"""

    __tablename__ = "service_catalog_items"
    __table_args__ = (
        Index("uq_service_catalog_store_name", "store_id", "name", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    price_cents = Column(Integer, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    description = Column(String(512), nullable=True)
    is_bookable = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoreBusinessHours(Base):
    """门店每周营业时间；weekday 使用 0=周一至 6=周日。"""

    __tablename__ = "store_business_hours"
    __table_args__ = (
        Index("uq_store_business_hours_day", "store_id", "weekday", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    weekday = Column(Integer, nullable=False)
    open_time = Column(String(5), nullable=True)
    close_time = Column(String(5), nullable=True)
    is_closed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoreAppointmentPolicy(Base):
    """门店预约政策。"""

    __tablename__ = "store_appointment_policies"
    __table_args__ = (Index("uq_store_appointment_policies_store", "store_id", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    min_notice_minutes = Column(Integer, nullable=False, default=120)
    cancel_window_minutes = Column(Integer, nullable=False, default=120)
    late_rule = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditEvent(Base):
    """商家关键操作审计事件。"""

    __tablename__ = "audit_events"

    id = Column(String(64), primary_key=True)
    actor_id = Column(Integer, ForeignKey("merchant_accounts.id"), nullable=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    action = Column(String(128), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(128), nullable=True)
    request_id = Column(String(128), nullable=True, index=True)
    outcome = Column(String(32), nullable=False)
    summary_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ConversationControl(Base):
    """会话当前控制状态；人工接管不是不可追踪的布尔字段。"""

    __tablename__ = "conversation_controls"

    conversation_id = Column(String(36), primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    mode = Column(String(24), nullable=False, default="ai_active")
    assignee_id = Column(Integer, ForeignKey("merchant_accounts.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConversationControlEvent(Base):
    """接管、恢复、备注和异常标记的不可变事件。"""

    __tablename__ = "conversation_control_events"

    id = Column(String(64), primary_key=True)
    conversation_id = Column(String(36), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("merchant_accounts.id"), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class StoreMembership(Base):
    """商家账号到门店的角色关系。"""

    __tablename__ = "store_memberships"
    __table_args__ = (
        Index("uq_store_memberships_actor_store", "actor_id", "store_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_id = Column(Integer, ForeignKey("merchant_accounts.id"), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    role = Column(String(24), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminSession(Base):
    """可撤销的服务端商家会话；数据库不保存原始 session/csrf token。"""

    __tablename__ = "admin_sessions"

    id = Column(String(64), primary_key=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    csrf_token_hash = Column(String(64), nullable=False)
    actor_id = Column(Integer, ForeignKey("merchant_accounts.id"), nullable=False, index=True)
    active_store_id = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)

class UserBehavior(Base):
    __tablename__ = 'user_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')  # 单用户场景使用默认用户ID
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    action_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'inquiry'
    action_data = Column(JSON, nullable=True)  # 存储行为相关的详细数据
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    technician = relationship("Technician")

class ConversationSummary(Base):
    """会话摘要快照表（Phase E E3）。

    - 覆盖范围由消息 sequence 确定（禁止用时间戳猜测范围）。
    - 同一会话保存多版本（新摘要写入前不覆盖旧摘要；active 为唯一有效快照）。
    - invalidated：偏好删除等导致的失效（审计保留，永不进入 ContextPackage）。
    - failed：生成/校验失败（保留原始消息，fallback 路径继续用旧摘要）。
    """

    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False, index=True)
    from_sequence = Column(Integer, nullable=False)
    to_sequence = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    key_facts = Column(JSON, nullable=True)  # 结构化关键事实列表
    status = Column(String(16), nullable=False, default="active")  # active/invalidated/failed
    version = Column(Integer, nullable=False, default=1)
    model_provider = Column(String(32), nullable=False, default="fake")
    failure_log_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Preference(Base):
    """长期偏好表（Phase E E4：唯一持久化事实来源）。

    - 同一 user_id + preference_type 最多一个 active 值（覆盖语义，决策四）。
    - source 标记来源（explicit_memorize / business_confirmation / legacy_unverified）。
    - 删除 = 置 inactive + 写墓碑（原行保留用于审计）。
    """

    __tablename__ = "preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, default="default_user", index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    preference_type = Column(String(32), nullable=False)
    preference_value = Column(Text, nullable=False)
    source = Column(String(32), nullable=False, default="explicit_memorize")
    source_message_id = Column(String(36), nullable=True)
    source_appointment_id = Column(String(36), nullable=True)
    confidence = Column(Integer, default=5)  # 0-100：00 未确认历史 -> 低值；显式确认 -> 100
    last_confirmed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Integer, default=1)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PreferenceTombstone(Base):
    """偏好删除墓碑表（Phase E E4：防旧缓存/旧摘要/并发读取重新激活）。"""

    __tablename__ = "preference_tombstones"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=True, index=True)
    preference_type = Column(String(32), nullable=False)
    normalized_value = Column(Text, nullable=False)
    original_preference_id = Column(Integer, nullable=True)
    source_message_id = Column(String(36), nullable=True)
    source_appointment_id = Column(String(36), nullable=True)
    deleted_at = Column(DateTime, default=datetime.utcnow)


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
