"""Phase I I6-E23/E24：SQLite 一致性备份、校验与恢复。

- 备份用 SQLite 官方 backup API（不复制正在写入的库文件），保证一致性。
- 备份清单（manifest）：schema 版本、知识版本、时间、sha256，供校验/追踪。
- 校验：PRAGMA integrity_check + 核心表抽查；不以"文件存在"验收。
- 恢复：默认 dry-run；目标路径独立，支持隔离目录演练。

用法：
    python -m scripts.backup backup <out_dir>
    python -m scripts.backup verify <backup.db>
    python -m scripts.backup restore <backup.db> <target.db> [--execute]
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

REQUIRED_TABLES = ("conversations", "messages", "appointments",
                   "stores", "merchant_accounts", "knowledge_documents")


def resolve_sqlite_path() -> str:
    from db.base.session_manager import SessionManager

    sm = SessionManager()
    url = sm.db_path
    sm.close()
    # url 形如 sqlite:///... 或 sqlite+immediate:///...；其余（非 sqlite）抛错
    if url.startswith(("sqlite", "sqlite+immediate")):
        prefix = ":///"
        if prefix in url:
            return url.split(prefix, 1)[1]
        return url
    raise RuntimeError("备份仅支持 SQLite 数据库（当前连接非 sqlite）")


def _manifest(src_path: str, dst_path: str) -> dict:
    with open(dst_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {
        "source": os.path.basename(src_path),
        "backup": os.path.basename(dst_path),
        "sha256": digest,
        "size": os.path.getsize(dst_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "phase-i",
    }


def backup(out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    src_path = resolve_sqlite_path()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst_path = os.path.join(out_dir, f"backup-{ts}.db")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)  # SQLite 一致性备份 API
        dst.commit()
    finally:
        dst.close()
        src.close()
    manifest = _manifest(src_path, dst_path)
    with open(dst_path + ".manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def verify(backup_path: str) -> dict:
    con = sqlite3.connect(backup_path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        missing = [t for t in REQUIRED_TABLES
                   if con.execute(
                       "SELECT name FROM sqlite_master WHERE type='table' AND name=:t",
                       {"t": t}).fetchone() is None]
    finally:
        con.close()
    sha = None
    man = backup_path + ".manifest.json"
    if os.path.exists(man):
        with open(man, encoding="utf-8") as f:
            sha = json.load(f).get("sha256")
    with open(backup_path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    ok = integrity == "ok" and not missing and (sha is None or sha == actual)
    return {"integrity": integrity, "missing_tables": missing,
            "checksum_match": (sha is None) or (sha == actual),
            "ok": ok}


def restore(backup_path: str, target_path: str, dry_run: bool = True) -> dict:
    v = verify(backup_path)
    if not v["integrity"] == "ok":
        raise RuntimeError(f"备份校验失败：{v}")
    if dry_run:
        return {"dry_run": True, "target": target_path, "verify": v}
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    shutil.copyfile(backup_path, target_path)
    return {"dry_run": False, "target": target_path, "verify": v}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase I 备份/校验/恢复")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup").add_argument("out_dir")
    sub.add_parser("verify").add_argument("backup_db")
    rp = sub.add_parser("restore")
    rp.add_argument("backup_db")
    rp.add_argument("target_db")
    rp.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.cmd == "backup":
        print(json.dumps(backup(args.out_dir), ensure_ascii=False, indent=2))
    elif args.cmd == "verify":
        print(json.dumps(verify(args.backup_db), ensure_ascii=False, indent=2))
    elif args.cmd == "restore":
        print(json.dumps(restore(args.backup_db, args.target_db,
                                 dry_run=not args.execute), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
