"""Phase I I3-E12：健康/就绪端点（D12）。

- GET /health/live  ：进程存活（对外可路由）。
- GET /health/ready ：可承接当前支持范围请求（DB 可达、迁移后表可用、知识索引版本可解析）。
  就绪失败返回 503，不返回伪健康。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from db.base.session_manager import SessionManager

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("/live")
def live():
    return {"status": "ok", "service": "ai-front-desk"}


@router.get("/ready")
def ready():
    checks = {"database": False, "migration": False, "knowledge_version": None}
    reason = []
    sm = SessionManager()
    try:
        with sm.session_scope() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = True
            # 迁移是否就绪：抽查核心表是否存在
            for table in ("conversations", "appointments", "stores"):
                row = session.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
                ), {"t": table}).fetchone()
                if row is None:
                    reason.append(f"missing_table:{table}")
            checks["migration"] = not reason
            # 知识版本：读取最新 knowledge_meta 里的知识版本
            version = session.execute(text(
                "SELECT value FROM knowledge_meta WHERE key='knowledge_version' "
                "ORDER BY updated_at DESC LIMIT 1"
            )).fetchone()
            if version is not None and version[0] is not None:
                checks["knowledge_version"] = version[0]
            else:
                # 无发布记录也认为结构就绪（空库可启动），但标记为 unknown
                checks["knowledge_version"] = "unknown"
            if not checks["database"] or not checks["migration"]:
                reason.append("database_or_migration_unavailable")
    except Exception:
        reason.append("database_unreachable")
    finally:
        sm.close()

    if not checks["database"] or not checks["migration"]:
        return JSONResponse(status_code=503, content={"ready": False, "checks": checks, "reason": reason})
    return JSONResponse(status_code=200, content={"ready": True, "checks": checks})
