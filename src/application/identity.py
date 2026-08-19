"""Phase E E5 前置：身份解析边界（决策三）。

当前无鉴权（约束 4），本地演示适配器从服务端配置解析固定 DEFAULT_USER_ID；
请求体中的 user_id 只作为兼容字段，必须与已解析身份一致，否则拒绝，
不得据此切换身份。后续接入真实鉴权时只替换 resolver，不改偏好/上下文归属校验。
"""

from abc import ABC, abstractmethod


class IdentityError(Exception):
    """身份解析/归属校验失败。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class IdentityResolver(ABC):
    """身份解析抽象：输入请求体兼容 user_id，输出服务端可信身份。"""

    @abstractmethod
    def resolve(self, request_user_id: str) -> str:
        """解析当前用户身份；兼容字段与已解析身份不一致时抛 IdentityError。"""


class DemoIdentityResolver(IdentityResolver):
    """本地演示适配器：固定 DEFAULT_USER_ID，任何人可声明的兼容 user_id 必须一致。"""

    def __init__(self, default_user_id: str = "default_user"):
        self.default_user_id = default_user_id

    def resolve(self, request_user_id: str) -> str:
        if request_user_id != self.default_user_id:
            raise IdentityError(f"身份校验失败：兼容字段 {request_user_id!r} 与已解析身份不一致")
        return self.default_user_id