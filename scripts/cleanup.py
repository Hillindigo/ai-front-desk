"""Phase I I2-E9：数据保留/清理命令（默认 dry-run）。

用法：
    python -m scripts.cleanup --dry-run            # 仅统计，不写入（默认）
    python -m scripts.cleanup --execute            # 显式授权后真实清理（带审计）

当前清理项：
- 超期草稿/待确认预约（复用 appointment_draft_cleanup 的保留语义）。
- 报告各 PII 实体中的留存计数（不打印内容，只打印数量）。

破坏性操作一律默认 dry-run；真实执行需 --execute 显式授权。
"""

import argparse
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from db.base.session_manager import SessionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup")

DRAFT_TTL_HOURS = 24  # 草稿/待确认超期阈值


def collect_report(sm: SessionManager) -> dict:
    """汇总可清理/留存对象数量（只计数，不回显内容）。"""
    with sm.session_scope() as session:
        expired_drafts = session.execute(text(
            "SELECT COUNT(*) FROM appointments "
            "WHERE status IN ('draft','pending_confirmation') "
            "AND expires_at IS NOT NULL AND expires_at < :now"
        ), {"now": datetime.utcnow()}).scalar()

        legacy_preferences = session.execute(text(
            "SELECT COUNT(*) FROM user_preferences"
        )).scalar()

        deleted_registry = session.execute(text(
            "SELECT COUNT(*) FROM privacy_deletion_registry"
        )).scalar()

        return {
            "expired_drafts": expired_drafts,
            "legacy_user_preferences": legacy_preferences,
            "deletion_registry_entries": deleted_registry,
        }


def purge_expired_drafts(sm: SessionManager) -> int:
    """删除超期草稿/待确认预约（真实执行）。"""
    with sm.session_scope() as session:
        return session.execute(text(
            "DELETE FROM appointments "
            "WHERE status IN ('draft','pending_confirmation') "
            "AND expires_at IS NOT NULL AND expires_at < :now"
        ), {"now": datetime.utcnow()}).rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase I 数据清理（默认 dry-run）")
    parser.add_argument("--execute", action="store_true",
                        help="真实执行清理（默认仅 dry-run 报告）")
    parser.add_argument("--draft-ttl-hours", type=int, default=DRAFT_TTL_HOURS)
    args = parser.parse_args()

    sm = SessionManager()
    try:
        report = collect_report(sm)
        logger.info("dry_run=%s report=%s", not args.execute, report)
        if args.execute:
            purged = purge_expired_drafts(sm)
            logger.info("purged_expired_drafts=%s", purged)
    finally:
        sm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
