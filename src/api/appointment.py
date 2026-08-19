"""
预约兼容 API（Phase C C6：降级为领域服务适配器）

旧 /api/appointment/create 不再实例化 Agent，而是转换为领域命令：
- service_type / user_id -> Appointment(draft)（service_type 落库）
- preferred_time 为自然语言字符串，无法可靠解析为 datetime，不落库
  （在返回中说明待补充时间），不再伪造预约成功。
"""

from fastapi import APIRouter, HTTPException

from .core.response_models import AppointmentRequest, DataResponse
from services.appointment_domain import AppointmentCommandService, AppointmentDomainError

router = APIRouter(prefix="/api/appointment", tags=["预约管理"])


@router.post("/create", response_model=DataResponse)
async def create_appointment(request: AppointmentRequest):
    """创建预约（兼容入口：转换为领域草稿命令）"""
    try:
        svc = AppointmentCommandService()
        try:
            draft = svc.create_draft(
                user_id=request.user_id,
                conversation_id=None,
                service_type=request.service_type,
                fields={"project": request.service_type},
            )
            return DataResponse(
                message="预约草稿已创建（旧接口兼容模式），请通过会话继续补充时间和服务人员信息",
                data=draft,
            )
        except AppointmentDomainError as e:
            raise HTTPException(status_code=400, detail=f"{e.code}: {e.message}")
        finally:
            svc.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
