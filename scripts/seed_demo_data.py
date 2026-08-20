"""Seed a coherent local demo dataset for the merchant account ``admin``.

The script is idempotent for the demo store and never deletes existing data.
Run with the project virtualenv:
    .venv/Scripts/python.exe scripts/seed_demo_data.py
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db.base.session_manager import SessionManager  # noqa: E402
from db.models import (  # noqa: E402  # noqa: E402
    Appointment,
    AppointmentEvent,
    Conversation,
    KnowledgeDocument,
    Message,
    ServiceCatalogItem,
    Store,
    StoreAppointmentPolicy,
    StoreBusinessHours,
    StoreProfile,
    Technician,
    TechnicianSchedule,
    UserBehavior,
)
from services.admin_auth import AdminAuthService  # noqa: E402

DEMO_KNOWLEDGE = [
    (
        "营业时间",
        "门店每天 09:00-22:00 营业，节假日如有调整会提前公告。",
        ["营业时间", "开门", "几点"],
    ),
    (
        "门店地址",
        "演示门店位于上海市静安区愚园路 88 号，地铁 2 号线静安寺站 4 号口步行约 8 分钟。",
        ["地址", "交通", "地铁"],
    ),
    (
        "服务项目",
        "肩颈放松 80 元/30 分钟，基础护理 120 元/60 分钟，足部护理 100 元/45 分钟，深度放松 180 元/90 分钟。",
        ["项目", "价格", "服务"],
    ),
    (
        "预约规则",
        "预约至少提前 2 小时提交；如需取消或改期，请至少提前 2 小时联系门店。",
        ["预约", "取消", "改期"],
    ),
    (
        "服务说明",
        "肩颈放松适合久坐、伏案和肩颈紧张人群；具体体验请以服务人员现场判断为准，不替代医疗诊断。",
        ["肩颈", "久坐", "效果"],
    ),
    (
        "会员权益",
        "会员充值 500 元赠 50 元，充值 1000 元赠 150 元，并享受预约优先权。",
        ["会员", "充值", "优惠"],
    ),
]

TECHNICIANS = [
    ("林晓", "女", "肩颈放松、精油护理"),
    ("陈安", "男", "足部护理、深度放松"),
    ("周宁", "女", "基础护理、肩颈放松"),
]

CATALOG = [
    ("肩颈放松", 8000, 30, "适合久坐和伏案人群的舒缓项目"),
    ("基础护理", 12000, 60, "日常放松与基础护理"),
    ("足部护理", 10000, 45, "足部舒缓和日常放松"),
    ("深度放松", 18000, 90, "更完整的全身放松体验"),
]


def get_demo_store_id(session_manager: Any) -> int:

    auth = AdminAuthService()
    try:
        identity = auth.authenticate("admin", "123")
        if identity:
            return int(identity["active_store"]["store_id"])
    finally:
        auth.close()
    with session_manager.session_scope() as session:
        store = (
            session.query(Store)
            .filter(Store.name == "演示门店")
            .order_by(Store.id.desc())
            .first()
        )
        if not store:
            raise RuntimeError("找不到 admin 账号或演示门店，请先创建 admin/123")
        store_id = getattr(store, "id", None)
        if not isinstance(store_id, int):
            raise RuntimeError("演示门店 ID 无效")
        return store_id


def seed() -> dict[str, int]:
    db = SessionManager()
    store_id = get_demo_store_id(db)
    now = datetime.utcnow()
    counts = {"knowledge": 0, "technicians": 0, "conversations": 0, "appointments": 0}

    with db.session_scope() as session:
        store = session.get(Store, store_id)
        if store is None:
            raise RuntimeError(f"门店不存在: {store_id}")
        profile = session.query(StoreProfile).filter_by(store_id=store_id).first()
        if not profile:
            session.add(
                StoreProfile(
                    store_id=store_id,
                    address="上海市静安区愚园路 88 号",
                    phone="021-60001234",
                    is_open=1,
                )
            )

        policy = (
            session.query(StoreAppointmentPolicy).filter_by(store_id=store_id).first()
        )
        if not policy:
            session.add(
                StoreAppointmentPolicy(
                    store_id=store_id,
                    min_notice_minutes=120,
                    cancel_window_minutes=120,
                    late_rule="迟到超过 15 分钟请先联系门店确认。",
                )
            )

        for weekday in range(7):
            hours = (
                session.query(StoreBusinessHours)
                .filter_by(store_id=store_id, weekday=weekday)
                .first()
            )
            if not hours:
                session.add(
                    StoreBusinessHours(
                        store_id=store_id,
                        weekday=weekday,
                        open_time="09:00",
                        close_time="22:00",
                        is_closed=0,
                    )
                )

        for title, content, keywords in DEMO_KNOWLEDGE:
            exists = (
                session.query(KnowledgeDocument)
                .filter_by(store_id=store_id, title=title, is_active=1)
                .first()
            )
            if not exists:
                session.add(
                    KnowledgeDocument(
                        store_id=store_id,
                        title=title,
                        content=content,
                        category=title,
                        keywords=keywords,
                        embedding=[0.1] * 128,
                        status="published",
                        document_version=1,
                        source_type="demo_seed",
                        source_label="本地演示数据",
                        created_by="system",
                        updated_by="system",
                        published_at=now,
                    )
                )
                counts["knowledge"] += 1

        tech_rows = []
        for name, gender, strength in TECHNICIANS:
            tech = (
                session.query(Technician)
                .filter_by(store_id=store_id, name=name)
                .first()
            )
            if not tech:
                tech = Technician(
                    store_id=store_id, name=name, gender=gender, strength=strength
                )
                session.add(tech)
                session.flush()
                counts["technicians"] += 1
            tech_rows.append(tech)

        for name, price, duration, description in CATALOG:
            item = (
                session.query(ServiceCatalogItem)
                .filter_by(store_id=store_id, name=name)
                .first()
            )
            if not item:
                session.add(
                    ServiceCatalogItem(
                        store_id=store_id,
                        name=name,
                        price_cents=price,
                        duration_minutes=duration,
                        description=description,
                        is_bookable=1,
                        is_active=1,
                    )
                )

        for tech in tech_rows:
            busy_start = (now + timedelta(days=1)).replace(
                hour=15, minute=0, second=0, microsecond=0
            )
            busy_end = busy_start + timedelta(minutes=60)
            existing = (
                session.query(TechnicianSchedule)
                .filter_by(technician_id=tech.id, status="busy")
                .filter(TechnicianSchedule.start_time == busy_start)
                .first()
            )
            if not existing:
                session.add(
                    TechnicianSchedule(
                        technician_id=tech.id,
                        start_time=busy_start,
                        end_time=busy_end,
                        status="busy" if tech.name == "陈安" else "free",
                    )
                )

        conversation_specs = [
            (
                "demo_customer_001",
                "价格和营业时间",
                "请问肩颈放松多少钱？你们几点营业？",
                "肩颈放松 80 元/30 分钟，门店每天 09:00-22:00 营业。",
            ),
            (
                "demo_customer_002",
                "预约咨询",
                "我想明天下午预约肩颈放松，女服务人员。",
                "可以为您安排肩颈放松，请确认具体时间和服务时长。",
            ),
            (
                "demo_customer_003",
                "地址咨询",
                "门店地址在哪里，地铁怎么过去？",
                "演示门店位于上海市静安区愚园路 88 号，地铁 2 号线静安寺站 4 号口步行约 8 分钟。",
            ),
        ]
        conversations = []
        for user_id, subject, user_text, assistant_text in conversation_specs:
            conv = (
                session.query(Conversation)
                .filter_by(store_id=store_id, user_id=user_id)
                .first()
            )
            if not conv:
                conv = Conversation(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    store_id=store_id,
                    channel="web",
                    status="active",
                    created_at=now,
                    updated_at=now,
                    last_activity_at=now,
                )
                session.add(conv)
                session.flush()
                session.add_all(
                    [
                        Message(
                            conversation_id=conv.id,
                            role="user",
                            content=user_text,
                            message_type="text",
                            sequence=1,
                            created_at=now,
                        ),
                        Message(
                            conversation_id=conv.id,
                            role="assistant",
                            content=assistant_text,
                            message_type="text",
                            sequence=2,
                            created_at=now,
                        ),
                    ]
                )
                session.add(
                    UserBehavior(
                        user_id=user_id,
                        store_id=store_id,
                        action_type="consultation",
                        action_data={"subject": subject},
                        session_id=conv.id,
                        created_at=now,
                    )
                )
                counts["conversations"] += 1
            conversations.append(conv)

        appointment = (
            session.query(Appointment)
            .filter_by(store_id=store_id, user_id="demo_customer_002")
            .first()
        )
        if not appointment:
            start = (now + timedelta(days=2)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(minutes=30)
            tech = tech_rows[0]
            appointment = Appointment(
                id=str(uuid.uuid4()),
                user_id="demo_customer_002",
                store_id=store_id,
                conversation_id=conversations[1].id,
                service_type="肩颈放松",
                project="肩颈放松",
                technician_id=tech.id,
                start_time=start,
                end_time=end,
                duration_minutes=30,
                status="confirmed",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(appointment)
            session.flush()
            session.add(
                AppointmentEvent(
                    appointment_id=appointment.id,
                    event_type="created",
                    to_status="confirmed",
                    payload_json='{"source":"demo_seed"}',
                    created_at=now,
                )
            )
            counts["appointments"] += 1

    db.close()
    return {"store_id": store_id, **counts}


if __name__ == "__main__":
    print(seed())
