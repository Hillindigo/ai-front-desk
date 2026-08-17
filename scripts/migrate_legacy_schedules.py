"""C0 D1：旧 busy 排班记录归档迁移（一次性脚本）。

背景：Phase C 之前 `technician_schedules.status='busy'` 兼任"预约记录"，
其中的 appointment_id 为时间戳整数、无归属、无法映射为 Appointment。
按 C0 决策 D1，把这类 legacy busy 标记为 status='archived'：
保留记录供审计，不静默删除；归档后新领域可用性查询不再把它们当作占用。

运行：python scripts/migrate_legacy_schedules.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from db.db_router import DatabaseRouter


def main() -> None:
    router = DatabaseRouter()
    try:
        with router.session_manager.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, technician_id, start_time, end_time, appointment_id "
                    "FROM technician_schedules WHERE status = 'busy'"
                )
            ).fetchall()
            if not rows:
                print("无 legacy busy 记录，跳过。")
                return
            for row in rows:
                conn.execute(
                    text("UPDATE technician_schedules SET status = 'archived' WHERE id = :id"),
                    {"id": row[0]},
                )
            conn.commit()
            print(f"已归档 {len(rows)} 条 legacy busy 记录（status -> archived）：")
            for row in rows:
                print(f"  id={row[0]} technician={row[1]} {row[2]} ~ {row[3]} "
                      f"appointment_id={row[4]}")
    finally:
        router.close()


if __name__ == "__main__":
    main()
