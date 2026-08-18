"""Phase I I1-E4：日志/错误脱敏工具。

防止 API Key、Bearer 令牌、密钥与长随机令牌写入服务端日志或错误响应。
业务代码/测试仍可读取原始值；仅在记录日志或回显前调用 redact()。
"""

from __future__ import annotations

import re

# 匹配各类疑似密钥/令牌：sk-/pk- 前缀、key=value、长 base64、64 位 hex。
_PATTERNS = [
    re.compile(r"\b(sk|pk|rk|ak)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(api[-_]?key|secret|password|token|authorization|session)\s*[=:]\s*[\"']?[\w./+\-=]{8,}"),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"\b[a-f0-9]{64}\b"),
]


def redact(text: object, placeholder: str = "<REDACTED>") -> str:
    """清扫文本中的疑似敏感值；非字符串输入先转字符串。空值返回空串。"""
    if text is None:
        return ""
    out = str(text)
    if not out:
        return out
    for pattern in _PATTERNS:
        out = pattern.sub(placeholder, out)
    return out
