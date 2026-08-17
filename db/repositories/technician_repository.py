from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BaseTechnicianRepository, BaseScheduleRepository
from ..base.session_manager import SessionManager
from ..models import Technician, TechnicianSchedule


class TechnicianRepository(BaseTechnicianRepository, BaseScheduleRepository):
    """
    服务人员数据访问对象
    
    职责：
    1. 服务人员信息的CRUD操作
    2. 服务人员排班的管理
    3. 服务人员可用性检查
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化服务人员数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_technician(self, name: str, gender: Optional[str] = None, strength: Optional[str] = None) -> int:
        """
        添加新服务人员
        
        Args:
            name: 服务人员姓名
            gender: 性别
            strength: 专长
            
        Returns:
            新创建的服务人员ID
        """
        with self.session_manager.session_scope() as session:
            technician = Technician(name=name, gender=gender, strength=strength)
            session.add(technician)
            session.flush()
            return technician.id

    def get_technician_by_id(self, technician_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取服务人员信息
        
        Args:
            technician_id: 服务人员ID
            
        Returns:
            服务人员信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            technician = session.query(Technician).filter(
                Technician.id == technician_id
            ).first()
            
            if not technician:
                return None
                
            return self._technician_to_dict(technician)

    def get_technician_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        根据姓名获取服务人员信息
        
        Args:
            name: 服务人员姓名
            
        Returns:
            服务人员信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            technician = session.query(Technician).filter(
                Technician.name == name
            ).first()
            
            if not technician:
                return None
                
            return self._technician_to_dict(technician)

    def get_all_technicians(self) -> List[Dict[str, Any]]:
        """
        获取所有服务人员信息
        
        Returns:
            服务人员信息列表
        """
        with self.session_manager.session_scope() as session:
            technicians = session.query(Technician).all()
            return [self._technician_to_dict(tech) for tech in technicians]

    def get_all_strengths(self) -> List[str]:
        """
        获取所有服务人员的专长列表
        
        Returns:
            专长列表（去重后）
        """
        with self.session_manager.session_scope() as session:
            strengths = session.query(Technician.strength).distinct().all()
            return [s[0] for s in strengths if s[0] is not None]

    def update_technician(self, technician_id: int, **updates) -> bool:
        """
        更新服务人员信息
        
        Args:
            technician_id: 服务人员ID
            **updates: 要更新的字段
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            technician = session.query(Technician).filter(
                Technician.id == technician_id
            ).first()
            
            if not technician:
                return False
                
            for key, value in updates.items():
                if hasattr(technician, key):
                    setattr(technician, key, value)
                    
            return True

    def delete_technician(self, technician_id: int) -> bool:
        """
        删除服务人员
        
        Args:
            technician_id: 服务人员ID
            
        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            technician = session.query(Technician).filter(
                Technician.id == technician_id
            ).first()
            
            if not technician:
                return False
                
            session.delete(technician)
            return True

    # 排班相关方法
    def add_schedule(self, technician_id: int, start_time: datetime, end_time: datetime, 
                    status: str, appointment_id: Optional[int] = None) -> int:
        """
        添加服务人员排班
        
        Args:
            technician_id: 服务人员ID
            start_time: 开始时间
            end_time: 结束时间
            status: 状态 ('busy' 或 'free')
            appointment_id: 预约ID（如果是忙碌状态）
            
        Returns:
            新创建的排班ID
        """
        with self.session_manager.session_scope() as session:
            schedule = TechnicianSchedule(
                technician_id=technician_id,
                start_time=start_time,
                end_time=end_time,
                status=status,
                appointment_id=appointment_id
            )
            session.add(schedule)
            session.flush()
            return schedule.id

    def get_technician_schedules(self, technician_id: int, date: datetime) -> List[Dict[str, Any]]:
        """
        获取服务人员指定日期的排班
        
        Args:
            technician_id: 服务人员ID
            date: 查询日期
            
        Returns:
            排班信息列表
        """
        with self.session_manager.session_scope() as session:
            start = datetime(date.year, date.month, date.day)
            end = start + timedelta(days=1)
            
            schedules = session.query(TechnicianSchedule).filter(
                TechnicianSchedule.technician_id == technician_id,
                TechnicianSchedule.start_time >= start,
                TechnicianSchedule.end_time < end
            ).all()
            
            return [self._schedule_to_dict(schedule) for schedule in schedules]

    def is_technician_available(self, technician_id: int, start_time: datetime, end_time: datetime) -> bool:
        """
        检查服务人员在指定时间段是否可用
        
        Args:
            technician_id: 服务人员ID
            start_time: 开始时间
            end_time: 结束时间
            
        Returns:
            是否可用
        """
        with self.session_manager.session_scope() as session:
            conflict = session.query(TechnicianSchedule).filter(
                TechnicianSchedule.technician_id == technician_id,
                TechnicianSchedule.status == "busy",
                TechnicianSchedule.start_time < end_time,
                TechnicianSchedule.end_time > start_time
            ).first()
            
            return conflict is None

    def find_schedule_conflicts(self, technician_id: int, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """查询服务人员排班（status='busy'）在 [start_time, end_time) 内的冲突块（Phase C C3）。

        排班约束语义：'busy' 表示不可用/休息/被占用时间块（半开区间）。
        """
        with self.session_manager.session_scope() as session:
            conflicts = session.query(TechnicianSchedule).filter(
                TechnicianSchedule.technician_id == technician_id,
                TechnicianSchedule.status == "busy",
                TechnicianSchedule.start_time < end_time,
                TechnicianSchedule.end_time > start_time
            ).all()
            return [self._schedule_to_dict(s) for s in conflicts]

    def update_schedule_status(self, schedule_id: int, status: str, appointment_id: Optional[int] = None) -> bool:
        """
        更新排班状态
        
        Args:
            schedule_id: 排班ID
            status: 新状态
            appointment_id: 预约ID
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(TechnicianSchedule).filter(
                TechnicianSchedule.id == schedule_id
            ).first()
            
            if not schedule:
                return False
                
            schedule.status = status
            if appointment_id is not None:
                schedule.appointment_id = appointment_id
                
            return True

    def delete_schedule(self, schedule_id: int) -> bool:
        """
        删除排班
        
        Args:
            schedule_id: 排班ID
            
        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(TechnicianSchedule).filter(
                TechnicianSchedule.id == schedule_id
            ).first()
            
            if not schedule:
                return False
                
            session.delete(schedule)
            return True

    def get_technicians_by_gender(self, gender: str) -> List[Dict[str, Any]]:
        """
        根据性别获取服务人员信息
        
        Args:
            gender: 服务人员性别
            
        Returns:
            服务人员信息列表
        """
        with self.session_manager.session_scope() as session:
            technicians = session.query(Technician).filter(
                Technician.gender == gender
            ).all()
            return [self._technician_to_dict(tech) for tech in technicians]

    def _technician_to_dict(self, technician: Technician) -> Dict[str, Any]:
        """将服务人员对象转换为字典"""
        return {
            'id': technician.id,
            'name': technician.name,
            'gender': technician.gender,
            'strength': technician.strength
        }

    def _schedule_to_dict(self, schedule: TechnicianSchedule) -> Dict[str, Any]:
        """将排班对象转换为字典"""
        return {
            'id': schedule.id,
            'technician_id': schedule.technician_id,
            'start_time': schedule.start_time,
            'end_time': schedule.end_time,
            'status': schedule.status,
            'appointment_id': schedule.appointment_id
        }
