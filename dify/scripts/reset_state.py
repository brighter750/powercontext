#!/usr/bin/env python3
"""Reset the local PowerContext state used by the innkeeper demo.

Deleting the SQLite file is not enough: PowerContext keeps a migration-state file and a
cursor key next to it. If those survive while the database disappears, the Server refuses to
start with ``recovery_required``. This script removes the whole set.

Usage:
    python reset_state.py --database-url sqlite+aiosqlite:///C:/path/powercontext-dify.db
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse


def sqlite_path(database_url: str) -> Path:
    parsed = urlparse(database_url)
    raw = unquote(parsed.path or "")
    if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    if not raw:
        raise SystemExit(f"无法从数据库 URL 解析出 SQLite 文件路径：{database_url}")
    return Path(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="清空忘忧酒肆演示用的 PowerContext 本地状态")
    parser.add_argument("--database-url", required=True, help="POWERCONTEXT_SERVER_DATABASE_URL")
    args = parser.parse_args()

    database = sqlite_path(args.database_url)
    sidecars = [
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
        database.with_name(database.name + ".pc-migration-state"),
        database.with_name("." + database.name + ".cursor-key"),
        database.with_name("." + database.name + ".cursor-key.lock"),
    ]
    for target in [database, *sidecars]:
        if not target.exists():
            print(f"跳过（不存在） {target}")
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except PermissionError as error:
            raise SystemExit(
                f"无法删除 {target}：文件被占用。请先停掉正在运行的 PowerContext Server 再执行本脚本。\n"
                f"（{error}）"
            ) from error
        print(f"已删除 {target}")
    print("PowerContext 本地状态已清空，下次启动会重新建库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
