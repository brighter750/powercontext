#!/usr/bin/env python3
"""Export the PowerContext Server access log of a verification run into a reviewable file.

The Server writes one JSON log line per transport request. This script keeps the lines that
describe Memory and Scope operations and prints them as a compact table, so a reviewer can
match each Dify HTTP node against a server-side record (including the request id).

Usage:
    python export_server_log.py --log-dir .artifacts/pc-logs --output evidence/cross-session/powercontext-server-log.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

INTERESTING_OPERATIONS = {
    "create_scope",
    "list_memory_entries",
    "remember_memory",
    "search_memory",
    "flush_memory",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 PowerContext 访问日志中的记忆与 Scope 操作")
    parser.add_argument("--log-dir", required=True, help="POWERCONTEXT_SERVER_LOGGING_DIRECTORY")
    parser.add_argument("--output", required=True, help="导出的证据文件路径")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    records: list[dict] = []
    for path in sorted(log_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "transport.request.completed":
                continue
            operation = event.get("operation", "")
            if operation not in INTERESTING_OPERATIONS:
                continue
            records.append(
                {
                    "timestamp": event.get("timestamp") or event.get("time") or event.get("asctime"),
                    "operation": operation,
                    "outcome": event.get("outcome"),
                    "status_code": event.get("status_code"),
                    "duration_ms": event.get("duration_ms"),
                    "request_id": event.get("request_id"),
                    "trace_id": event.get("trace_id"),
                }
            )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PowerContext Server 访问日志（记忆与 Scope 操作）",
        "# 由 dify/scripts/export_server_log.py 从 Server 的 JSON 日志中筛选生成",
        f"# 记录条数：{len(records)}",
        "",
    ]
    for index, record in enumerate(records, start=1):
        lines.append(
            f"{index:02d} {record['timestamp']} operation={record['operation']} "
            f"outcome={record['outcome']} status={record['status_code']} "
            f"duration_ms={record['duration_ms']} request_id={record['request_id']}"
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已导出 {len(records)} 条记录到 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
