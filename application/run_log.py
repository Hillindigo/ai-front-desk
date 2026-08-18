"""Phase I I3-E10/E11/E14：进程内 Agent 运行记录（观测埋点）。

- 低侵入：通过一个小型 ring buffer 记录每轮 run 的开始/结束/耗时/结果/失败类别。
- 不阻塞主业务：写入内存即可；不向 SSE 流路径加数据库写入。
- E14：观测写入不静默丢失 —— 超过缓冲上限或异常时增加 drop 计数并暴露。
- 指标标签不绑定 PII（只记 conversation_id、request_id、失败类别、工作流、耗时）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional


class RunRecorder:
    """线程安全的进程内运行记录（带上限与 drop 计数）。"""

    def __init__(self, max_entries: int = 5000):
        self.max_entries = max_entries
        self._entries: deque = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self.drop_count = 0

    def begin(self, conversation_id: str, request_id: Optional[str] = None,
              user_id: Optional[str] = None) -> Dict[str, Any]:
        """记录一轮开始的上下文；返回该 run 的条目引用。"""
        entry: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "request_id": request_id,
            "user_id": user_id,  # 仅作审计关联，不作为指标标签计数
            "started_at": time.time(),
            "duration_ms": None,
            "outcome": "running",
            "error_category": None,
            "workflow": None,
        }
        with self._lock:
            try:
                self._entries.append(entry)
            except Exception:
                self.drop_count += 1
        return entry

    def end(self, entry: Dict[str, Any], *, outcome: str,
            error_category: Optional[str] = None, workflow: Optional[str] = None):
        """结束一轮并写回结果。"""
        entry["duration_ms"] = int((time.time() - entry["started_at"]) * 1000)
        entry["outcome"] = outcome
        entry["error_category"] = error_category
        entry["workflow"] = workflow

    def entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def summary(self, limit: int = 200) -> Dict[str, Any]:
        """汇总：总数、结果分布、失败类别分布、平均/最慢耗时、drop 计数。"""
        entries = self.entries()
        if not entries:
            return {
                "total": 0, "outcomes": {}, "error_categories": {},
                "avg_duration_ms": None, "max_duration_ms": None,
                "drop_count": self.drop_count, "recent": [],
            }
        durations = [e["duration_ms"] for e in entries if e["duration_ms"] is not None]
        outcomes: Dict[str, int] = {}
        errors: Dict[str, int] = {}
        for e in entries:
            outcomes[e["outcome"]] = outcomes.get(e["outcome"], 0) + 1
            cat = e.get("error_category")
            if cat:
                errors[cat] = errors.get(cat, 0) + 1
        recent = sorted(entries, key=lambda e: e["started_at"], reverse=True)[:limit]
        return {
            "total": len(entries),
            "outcomes": outcomes,
            "error_categories": errors,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
            "max_duration_ms": max(durations) if durations else None,
            "drop_count": self.drop_count,
            "recent": [
                {"conversation_id": e.get("conversation_id"),
                 "request_id": e.get("request_id"),
                 "duration_ms": e.get("duration_ms"),
                 "outcome": e.get("outcome"),
                 "error_category": e.get("error_category"),
                 "workflow": e.get("workflow")}
                for e in recent
            ],
        }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.drop_count = 0
