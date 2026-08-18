"""Phase G G2：业务写入的默认门店解析。"""

from typing import Optional

from db.models import Store


def resolve_store_id(session, store_id: Optional[int] = None) -> int:
    """解析服务端门店 ID；兼容旧买家链路时使用唯一默认演示门店。"""
    if store_id is not None:
        store = session.query(Store).filter_by(id=store_id, is_active=1).first()
        if store is None:
            raise ValueError("门店不存在或已停用")
        return int(store.id)
    store = session.query(Store).filter_by(is_active=1).order_by(Store.id).first()
    if store is None:
        store = Store(name="默认演示门店", timezone="Asia/Shanghai", is_active=1)
        session.add(store)
        session.flush()
    return int(store.id)
