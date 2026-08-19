"""
预约服务层

职责：
1. 封装预约相关的数据库操作
2. 处理预约业务逻辑
3. 提供预约相关的数据服务
"""

import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class AppointmentService:
    """预约服务类"""
    
    def __init__(self, db_path: str | None = None):  # Phase B 决策一：None 时取 db_config
        self.db_router = DatabaseRouter(db_path)
        self.technician_repo = self.db_router.technicians
        self.appointment_repo = self.db_router.appointments  # Phase C：预约冲突查询

    # ---------------- Phase C C3：领域可用性 ----------------

    def check_technician_availability(self, technician_id: int, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """领域可用性检查：排班约束 + 已确认预约冲突（半开区间）。

        返回 {"available": bool, "reason": str | None}；
        reason 取值：TECHNICIAN_NOT_FOUND / TECHNICIAN_UNAVAILABLE / APPOINTMENT_CONFLICT。
        """
        tech = self.technician_repo.get_technician_by_id(technician_id)
        if tech is None:
            return {"available": False, "reason": "TECHNICIAN_NOT_FOUND"}
        if self.technician_repo.find_schedule_conflicts(technician_id, start_time, end_time):
            return {"available": False, "reason": "TECHNICIAN_UNAVAILABLE"}
        if self.appointment_repo.find_conflicts(technician_id, start_time, end_time):
            return {"available": False, "reason": "APPOINTMENT_CONFLICT"}
        return {"available": True, "reason": None}

    def get_available_technicians(self, start_time: datetime, end_time: datetime,
                                  gender: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询 [start_time, end_time) 内可用的服务人员列表（可选性别过滤）。"""
        techs = self.technician_repo.get_all_technicians()
        if gender:
            techs = [t for t in techs if t.get("gender") == gender]
        available = []
        for tech in techs:
            result = self.check_technician_availability(tech["id"], start_time, end_time)
            if result["available"]:
                available.append(tech)
        return available
    
    def save_appointment(self, technician_id: str, start_time: datetime, 
                        end_time: datetime, appointment_history: Dict[str, Any], 
                        session_id: str) -> bool:
        """保存预约信息到数据库"""
        try:
            appointment_id = int(time.time() * 1000)
            
            # 保存预约到数据库
            self.technician_repo.add_schedule(
                technician_id=int(technician_id),
                start_time=start_time,
                end_time=end_time,
                status="busy",
                appointment_id=appointment_id
            )
            
            logger.info(f"预约信息已保存到数据库：服务人员ID={technician_id}, 时间={start_time} 到 {end_time}, 预约ID={appointment_id}")
            return True
            
        except Exception as e:
            logger.error(f"保存预约信息到数据库失败：{e}")
            return False
    
    def get_technician_by_id(self, technician_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取服务人员信息"""
        try:
            return self.technician_repo.get_technician_by_id(technician_id)
        except Exception as e:
            logger.error(f"获取服务人员信息失败：{e}")
            return None
    
    def get_technician_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取服务人员信息"""
        try:
            return self.technician_repo.get_technician_by_name(name)
        except Exception as e:
            logger.error(f"获取服务人员信息失败：{e}")
            return None
    
    def get_all_technicians(self) -> List[Dict[str, Any]]:
        """获取所有服务人员信息"""
        try:
            return self.technician_repo.get_all_technicians()
        except Exception as e:
            logger.error(f"获取服务人员列表失败：{e}")
            return []
    
    def get_technicians_by_gender(self, gender: str) -> List[Dict[str, Any]]:
        """根据性别获取服务人员信息"""
        try:
            return self.technician_repo.get_technicians_by_gender(gender)
        except Exception as e:
            logger.error(f"根据性别获取服务人员信息失败：{e}")
            return []
    
    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取服务人员排班信息"""
        try:
            return self.technician_repo.get_technician_schedules(technician_id, date)
        except Exception as e:
            logger.error(f"获取服务人员排班信息失败：{e}")
            return []
    
    def is_technician_available(self, technician_id: int, start_time: datetime, end_time: datetime) -> bool:
        """检查服务人员是否可用"""
        try:
            return self.technician_repo.is_technician_available(technician_id, start_time, end_time)
        except Exception as e:
            logger.error(f"检查服务人员可用性失败：{e}")
            return False
    
    def add_technician(self, name: str, gender: str = None, strength: str = None) -> Optional[int]:
        """添加新服务人员"""
        try:
            return self.technician_repo.add_technician(name, gender, strength)
        except Exception as e:
            logger.error(f"添加服务人员失败：{e}")
            return None
    
    def get_all_strengths(self) -> List[str]:
        """获取所有服务人员的专长列表"""
        try:
            return self.technician_repo.get_all_strengths()
        except Exception as e:
            logger.error(f"获取服务人员专长列表失败：{e}")
            return []
