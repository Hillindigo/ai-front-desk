"""预约草稿 TTL 清理调度（Phase C）。"""

import asyncio
import logging
import os

from services.appointment_domain import AppointmentCommandService

logger = logging.getLogger(__name__)


def cleanup_interval_seconds() -> float:
    """读取清理周期；非法配置回退到一小时。"""
    try:
        return max(1.0, float(os.getenv("APPOINTMENT_DRAFT_CLEANUP_INTERVAL_SECONDS", "3600")))
    except ValueError:
        return 3600.0


async def appointment_draft_cleanup_loop(
    stop_event: asyncio.Event,
    interval_seconds: float | None = None,
) -> None:
    """启动一个可停止、可重复执行的预约草稿 TTL 清理循环。

    首次启动立即清理，之后按周期执行；数据库异常只记录日志，不阻断主应用。
    """
    interval = interval_seconds or cleanup_interval_seconds()
    while not stop_event.is_set():
        service = AppointmentCommandService()
        try:
            expired = service.expire_drafts()
            if expired:
                logger.info("预约草稿 TTL 清理完成：标记 %s 条 expired", expired)
        except Exception:
            logger.exception("预约草稿 TTL 清理失败，将在下一周期重试")
        finally:
            service.close()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
