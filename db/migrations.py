"""数据库轻量迁移（Phase F F1）。

`SessionManager` 用 `Base.metadata.create_all` 只建缺失表，不会给已有表补列。
本模块为既有 `knowledge_documents` 表补齐 Phase F 新增字段并回填旧数据，
支持 SQLite（PRAGMA table_info 探测 + ALTER TABLE ADD COLUMN）。

回填语义（计划 F1 验收：老数据可读但不被错误标为新发布版本）：
- 活跃旧行(is_active=1) -> status='published'（本就对外生效）
- 已软删旧行(is_active=0) -> status='archived'
- document_version=1、source_type='legacy'、published_at=updated_at
- 不伪造新的 knowledge_version / 发布时间
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 目标列：列名 -> (SQL 类型, backfill SQL 表达式或 None)
# 注意：SQLite 对 ALTER ADD COLUMN 的 NOT NULL 列会强制填充 DEFAULT，导致旧行
#     无法与"迁移后新建"区分。故新增列一律可空；回填只针对仍然 NULL 的旧行，
#     新建行由 ORM 层默认值（status='draft'、document_version=1）负责。
_NEW_COLUMNS = {
    "title": ("VARCHAR", None),
    "status": ("VARCHAR", None),
    "document_version": ("INTEGER", None),
    "knowledge_version": ("INTEGER", None),
    "source_type": ("VARCHAR", None),
    "source_label": ("VARCHAR", None),
    "created_by": ("VARCHAR", None),
    "updated_by": ("VARCHAR", None),
    "published_at": ("DATETIME", None),
    "archived_at": ("DATETIME", None),
}


def _existing_columns(conn, table: str):
    return {
        row[1]
        for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }


def apply_knowledge_migrations(engine) -> bool:
    """为 knowledge_documents 补齐新增列并回填；返回是否发生了 ALTER。

    幂等：列已存在则跳过；表不存在则创建由其调用方 create_all 处理。
    """
    with engine.begin() as conn:
        tables = {
            r[0]
            for r in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).fetchall()
        }
        if "knowledge_documents" not in tables:
            return False

        existing = _existing_columns(conn, "knowledge_documents")
        migrated = False
        for name, (sql_type, _backfill) in _NEW_COLUMNS.items():
            if name in existing:
                continue
            conn.execute(text(
                f"ALTER TABLE knowledge_documents ADD COLUMN {name} {sql_type}"
            ))
            migrated = True

        if migrated:
            # 回填：活跃->published，已软删->archived；版本与来源默认值。
            _backfill_status(conn)
        return migrated


def _backfill_status(conn) -> None:
    """为历史行回填状态 / 版本 / 来源 / 发布时间（不伪造 knowledge_version）。"""
    conn.execute(text(
        "UPDATE knowledge_documents SET status='published', document_version=1, "
        "source_type='legacy', source_label='旧数据迁移', published_at=updated_at "
        "WHERE status IS NULL AND is_active=1"
    ))
    conn.execute(text(
        "UPDATE knowledge_documents SET status='archived', document_version=1, "
        "source_type='legacy', source_label='旧数据迁移' "
        "WHERE status IS NULL AND is_active=0"
    ))
    # 双保险：任何仍为 NULL 的状态落为 draft（新建行默认）。
    conn.execute(text(
        "UPDATE knowledge_documents SET status='draft' WHERE status IS NULL"
    ))


# Phase G G2：单门店历史数据回填到默认门店
_STORE_SCOPED_TABLES = (
    "conversations",
    "appointments",
    "knowledge_documents",
    "knowledge_meta",
    "technicians",
    "user_behaviors",
    "preferences",
    "preference_tombstones",
)


def apply_store_migrations(engine) -> int:
    """为已有业务表补 store_id，并把历史单门店数据回填到默认门店。

    新增列保持可空以兼容旧 SQLite；应用层在 G2 收敛后禁止创建无门店范围的新业务数据。
    返回默认门店 ID。函数幂等，可安全重复执行。
    """
    with engine.begin() as conn:
        stores = conn.execute(text("SELECT id FROM stores ORDER BY id LIMIT 1")).fetchone()
        if stores is None:
            conn.execute(text(
                "INSERT INTO stores (name, timezone, is_active, created_at, updated_at) "
                "VALUES (:name, :timezone, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"name": "默认演示门店", "timezone": "Asia/Shanghai"})
            stores = conn.execute(text("SELECT id FROM stores ORDER BY id LIMIT 1")).fetchone()
        default_store_id = int(stores[0])

        tables = {
            row[0]
            for row in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).fetchall()
        }
        for table in _STORE_SCOPED_TABLES:
            if table not in tables:
                continue
            columns = _existing_columns(conn, table)
            if "store_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN store_id INTEGER"))
            conn.execute(text(
                f"UPDATE {table} SET store_id = :store_id WHERE store_id IS NULL"
            ), {"store_id": default_store_id})
        return default_store_id
