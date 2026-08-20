"""Run one isolated buyer or merchant demo process."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]

CONFIG = {
    "massage": {
        "buyer": ("demo/massage/massage.db", 8101),
        "admin": ("demo/massage/massage.db", 8102),
    },
    "beauty": {
        "buyer": ("demo/beauty/beauty.db", 8201),
        "admin": ("demo/beauty/beauty.db", 8202),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="启动隔离的按摩或美容演示服务")
    parser.add_argument("kind", choices=CONFIG, help="演示门店类型")
    parser.add_argument("role", choices=("buyer", "admin"), help="buyer 买家端；admin 商家后台端")
    parser.add_argument("--port", type=int, help="覆盖默认端口")
    args = parser.parse_args()

    relative_db, default_port = CONFIG[args.kind][args.role]
    os.environ.setdefault("MODEL_PROVIDER", "fake")
    os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
    os.environ["APP_ROLE"] = args.role
    os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / relative_db).as_posix()}"
    os.environ["DEMO_KIND"] = args.kind

    sys.path.insert(0, str(ROOT))
    port = args.port or default_port
    uvicorn.run("app:app", host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
