from enum import Enum

# 弃用（Phase C C3）：预约占用由 Appointment(confirmed) 承担，本字典不再被
# 新主流程写入/读取；保留定义仅兼容旧导出，后续阶段删除。
busy_periods_dict = {}  # { technician_id: [ {"start": "...", "end": "..."} ] }

class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    OTHER = "other"
    
class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY
