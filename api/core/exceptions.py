"""
API 异常处理（Phase I I1-E4：统一错误脱敏）

- 响应形状保持既有约定：业务/通用错误返回 {"error": ...}，HTTPException 的 4xx 由
  FastAPI 默认处理器返回 {"detail": ...}，二者都已在既有契约测试中固化，不改动。
- 服务端日志一律经 redact() 脱敏（API Key/Bearer/密钥/长令牌/PII），且只记
  path/method/错误类型，不回显请求体全文。
- 新增 RequestValidationError 处理器：422 校验错误不回显输入值，防止密钥/PII
  经校验回显泄漏；字段级错误仅记录位置与类型。
"""

import logging
import traceback

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.core.redact import redact

logger = logging.getLogger("api.errors")


class BusinessException(HTTPException):
    """业务逻辑异常（保持既有 HTTPException 语义）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(status_code=status_code, detail=message)


def _base_error_handler(request: Request, exc: Exception, *, status: int,
                        code: str, message: str, log_level: str) -> JSONResponse:
    """统一脱敏记录 + 稳定响应。"""
    tb = traceback.format_exc()
    record = (
        "api_error code=%s path=%s method=%s type=%s detail=%s%s",
        code,
        request.url.path,
        request.method,
        type(exc).__name__,
        redact(str(exc)),
        redact(tb[-3000:]) if tb and log_level == "error" else "",
    )
    getattr(logger, log_level)(*record)
    return JSONResponse(status_code=status, content={"error": message})


async def api_exception_handler(request: Request, exc: BusinessException):
    """业务异常：响应保持既有 {"error": detail}，日志脱敏。"""
    logger.error(
        "business_exception path=%s method=%s type=%s detail=%s",
        request.url.path, request.method, type(exc).__name__, redact(str(exc.detail)),
    )
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


async def general_exception_handler(request: Request, exc: Exception):
    """通用异常：不向客户端泄漏堆栈/路径/密钥，服务端日志经脱敏完整记录。"""
    return _base_error_handler(
        request, exc, status=500, code="INTERNAL_ERROR",
        message="服务器内部错误", log_level="error",
    )


async def request_validation_handler(request: Request, exc: RequestValidationError):
    """422 校验错误：不回显输入值；仅记录字段位置与类型（脱敏）。"""
    fields = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        fields.append({"loc": loc, "type": err.get("type", "")})
    logger.warning(
        "validation_error path=%s method=%s fields=%s",
        request.url.path, request.method, redact(str(fields)),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "INVALID_INPUT", "message": "请求参数无效"}},
    )
