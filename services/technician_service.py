# utils/ai/technician_service.py

from typing import List, Dict, Any, Optional
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class TechnicianService:
    """服务人员服务类 - 管理可预约人员数据和默认初始化"""
    
    def __init__(self):
        self.db = DatabaseRouter()
        
        # 默认服务人员数据（10人，其中有两位擅长内容接近）
        self.default_technicians = [
            {
                "name": "林舟",
                "gender": "男",
                "strength": "擅长深度放松和肩颈护理，服务节奏稳定，适合需要舒缓压力的客户"
            },
            {
                "name": "沈言",
                "gender": "男",
                "strength": "擅长运动后恢复和深度放松，服务流程清晰，注重客户反馈"
            },
            {
                "name": "顾宁",
                "gender": "女", 
                "strength": "服务细致，擅长舒缓放松，适合工作压力较大、希望安静体验的客户"
            },
            {
                "name": "叶澜",
                "gender": "女",
                "strength": "擅长肩颈护理和日常放松，沟通耐心，能够根据客户偏好调整服务节奏"
            },
            {
                "name": "程野",
                "gender": "男",
                "strength": "擅长拉伸类和全身放松项目，适合喜欢主动沟通和明确流程的客户"
            },
            {
                "name": "苏禾",
                "gender": "女",
                "strength": "擅长香氛护理和舒缓类项目，重视环境体验与服务细节"
            },
            {
                "name": "陆川",
                "gender": "男",
                "strength": "擅长肩颈与腰背放松类项目，服务前会主动确认客户需求"
            },
            {
                "name": "唐棠",
                "gender": "女",
                "strength": "擅长头部与足部护理，服务节奏舒缓，适合晚间放松场景"
            },
            {
                "name": "周野",
                "gender": "男",
                "strength": "擅长强度可调的深度放松项目，适合有明确力度偏好的客户"
            },
            {
                "name": "许安",
                "gender": "女",
                "strength": "擅长面部护理和形象管理类项目，适合注重仪式感与细节的客户"
            }
        ]

    def initialize_default_technicians(self) -> bool:
        """初始化默认服务人员数据"""
        try:
            # 检查是否已有服务人员数据
            existing_technicians = self.db.technicians.get_all_technicians()
            
            if existing_technicians:
                logger.info(f"数据库中已有 {len(existing_technicians)} 位服务人员，跳过初始化")
                return True
            
            logger.info("数据库中无服务人员数据，开始初始化默认服务人员")
            
            # 添加默认服务人员
            for tech_data in self.default_technicians:
                try:
                    tech_id = self.db.technicians.add_technician(
                        name=tech_data['name'],
                        gender=tech_data['gender'],
                        strength=tech_data['strength']
                    )
                    logger.debug(f"添加服务人员: {tech_data['name']} (ID: {tech_id})")
                    
                except Exception as e:
                    logger.error(f"添加服务人员 {tech_data['name']} 失败: {e}")
                    return False
            
            # 验证初始化结果
            final_count = len(self.db.technicians.get_all_technicians())
            logger.info(f"服务人员初始化完成，共添加 {final_count} 位服务人员")
            return True
            
        except Exception as e:
            logger.error(f"服务人员初始化失败: {e}")
            return False

    def get_all_technicians(self, store_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取所有服务人员信息"""
        return self.db.technicians.get_all_technicians(store_id=store_id)

    def get_technician_by_name(self, name: str) -> Dict[str, Any]:
        """根据姓名获取服务人员信息"""
        return self.db.technicians.get_technician_by_name(name)

    def get_technician_by_id(self, technician_id: int) -> Dict[str, Any]:
        """根据ID获取服务人员信息"""
        return self.db.technicians.get_technician_by_id(technician_id)

    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取服务人员指定日期的排班信息"""
        return self.db.technicians.get_technician_schedules(technician_id, date)

    def is_technician_available(self, technician_id: int, start_time, end_time) -> bool:
        """检查服务人员在指定时间段是否可用"""
        return self.db.technicians.is_technician_available(technician_id, start_time, end_time)

    def add_technician(self, name: str, gender: str = None, strength: str = None) -> int:
        """添加新服务人员"""
        return self.db.technicians.add_technician(name, gender, strength)

    def get_technicians_count(self) -> int:
        """获取服务人员总数"""
        technicians = self.db.technicians.get_all_technicians()
        return len(technicians)

    def get_technician_by_id(self, technician_id: int) -> Dict[str, Any]:
        """根据ID获取服务人员信息"""
        return self.db.technicians.get_technician_by_id(technician_id)

    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取服务人员指定日期的排班信息"""
        return self.db.technicians.get_technician_schedules(technician_id, date)

    def is_technician_available(self, technician_id: int, start_time, end_time) -> bool:
        """检查服务人员在指定时间段是否可用"""
        return self.db.technicians.is_technician_available(technician_id, start_time, end_time)
