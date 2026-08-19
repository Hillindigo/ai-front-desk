"""行为记录旁路（Phase D D6）。

- 注入式 BehaviorRecorder：记录失败只输出结构化日志，绝不阻断主对话结果。
- 不扩展为完整推荐/画像系统（A-R2 收口：边界 + 失败隔离，非功能扩展）。
"""

import json
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class BehaviorRecorder:
    """旁路行为记录器。

    默认实现：结构化日志（JSON 单行，可被日志平台检索）。
    可通过 record_fn 注入确定性记录实现（如未来的 repository 写入）。
    """

    def __init__(self, record_fn: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._record_fn = record_fn

    def record(
        self,
        user_id: str,
        conversation_id: str,
        action_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录一次行为；任何失败只记日志，不抛异常（旁路原则）。"""
        try:
            entry = {
                "event": "user_behavior",
                "user_id": user_id,
                "conversation_id": conversation_id,
                "action_type": action_type,
                "data": data or {},
            }
            if self._record_fn is not None:
                self._record_fn(entry)
            else:
                logger.info("BEHAVIOR %s", json.dumps(entry, ensure_ascii=False))
        except Exception:
            # 行为记录失败不影响主流程（Phase C/D 已确认原则）
            logger.warning("行为记录失败（旁路忽略）: user=%s action=%s", user_id, action_type, exc_info=True)
