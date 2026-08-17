"""
预约数据库操作器

负责处理预约相关的数据库操作
注意：现在通过Services层访问数据库，符合分层架构
"""

from typing import Dict, Any
import logging
from datetime import datetime
from config.time_config import time_config
logger = logging.getLogger(__name__)


class AppointmentDatabase:
    """预约数据库操作器"""
    
    def __init__(self):
        # 延迟导入Services避免循环依赖
        self._appointment_service = None
        self._user_behavior_service = None
    
    @property
    def appointment_service(self):
        """懒加载预约服务"""
        if self._appointment_service is None:
            from services.appointment_service import AppointmentService
            self._appointment_service = AppointmentService()
        return self._appointment_service
    
    @property 
    def user_behavior_service(self):
        """懒加载用户行为服务"""
        if self._user_behavior_service is None:
            from services.user_behavior_service import UserBehaviorService
            self._user_behavior_service = UserBehaviorService()
        return self._user_behavior_service
    
    def save_appointment(self, technician_id: str, start_time: datetime, 
                        end_time: datetime, appointment_history: Dict[str, Any], 
                        session_id: str) -> bool:
        """保存预约信息（Phase C C5 适配器：通过领域服务创建并确认预约）。

        不再直接写 technician_schedules；
        幂等键 = session_id，同一会话重复提交返回同一预约。
        """
        try:
            from services.appointment_domain import AppointmentCommandService, AppointmentDomainError

            svc = AppointmentCommandService()
            try:
                existing = svc.repo.get_by_idempotency("default_user", session_id)
                if existing is not None:
                    return existing["status"] == "confirmed"
                draft = svc.create_draft(
                    user_id="default_user",  # 兼容现状：单用户演示（Phase D 传入真实 user_id）
                    conversation_id=session_id,
                    service_type=appointment_history.get("project", "门店服务"),
                    fields={
                        "project": appointment_history.get("project"),
                        "technician_id": int(technician_id),
                        "start_time": start_time,
                        "end_time": end_time,
                        "duration_minutes": int((end_time - start_time).total_seconds() // 60),
                    },
                )
                pending = svc.request_confirmation(draft["id"], "default_user")
                svc.confirm(pending["id"], "default_user", idempotency_key=session_id)
                return True
            except AppointmentDomainError as e:
                logger.error(f"领域预约失败: {e.code} {e.message}")
                return False
            finally:
                svc.close()
        except Exception as e:
            logger.error(f"保存预约信息到数据库失败：{e}")
            return False

    def sync_draft(self, session_id: str, appointment_history: Dict[str, Any]) -> bool:
        """Phase C C5：把预约对话中已确定的结构化字段同步到会话的持久化草稿。

        用于服务重启后恢复未完成预约上下文（当前仅同步项目字段，
        其他字段由对话继续收集）。
        """
        project = (appointment_history or {}).get("project")
        if not project or project == "未知":
            return False
        try:
            from services.appointment_domain import AppointmentCommandService, AppointmentDomainError

            svc = AppointmentCommandService()
            try:
                existing = svc.get_active_draft(session_id)
                if existing is None:
                    svc.create_draft(
                        user_id="default_user",
                        conversation_id=session_id,
                        service_type=project,
                        fields={"project": project},
                    )
                else:
                    svc.update_draft(
                        existing["id"], "default_user", {"project": project, "service_type": project}
                    )
                return True
            except AppointmentDomainError as e:
                logger.error(f"同步预约草稿失败: {e.code} {e.message}")
                return False
            finally:
                svc.close()
        except Exception as e:
            logger.error(f"同步预约草稿失败：{e}")
            return False
    
    def _record_user_behavior(self, start_time: datetime, end_time: datetime,
                            technician_id: str, appointment_history: Dict[str, Any], 
                            session_id: str):
        """记录用户预约行为"""
        try:
            action_data = {
                'start_time': time_config.format_datetime(start_time, "%Y-%m-%d %H:%M:%S"),
                'end_time': time_config.format_datetime(end_time, "%Y-%m-%d %H:%M:%S"),
                'duration': int((end_time - start_time).total_seconds() / 60),
                'project': appointment_history.get('project', '门店服务'),
                'preference': appointment_history.get('preference', ''),
                'technician_id': technician_id
            }
            
            # 通过Services层记录用户行为
            self.user_behavior_service.record_behavior(
                user_id="default_user",  # 统一使用default_user作为用户ID
                action_type='appointment',
                action_data=action_data,
                technician_id=str(technician_id),
                session_id=session_id
            )
            
        except Exception as behavior_error:
            logger.error(f"记录用户行为失败（但预约仍然成功）：{behavior_error}")
