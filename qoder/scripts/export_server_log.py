"""Extract StudyFlow's memory operations from the PowerContext Server log.

The Server writes one structured line per transport request. This script pulls the
lines that belong to StudyFlow's own operations and stores them as evidence, so a
reviewer can confirm which PowerContext endpoints were called, with which request id
and status code, without rerunning anything.

Usage::

    python qoder/scripts/export_server_log.py [--log PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OPERATIONS = (
    "create_scope",
    "list_scopes",
    "get_scope",
    "set_scope_binding",
    "resolve_scope_binding",
    "remember_memory",
    "search_memory",
    "list_memory_entries",
    "get_artifact",
    "replace_artifact",
    "get_server_info",
)

FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_line(line: str) -> dict[str, str] | None:
    """Parse one structured log line into its key/value fields."""
    if "transport.request.completed" not in line:
        return None
    fields = {key: value.strip('"') for key, value in FIELD.findall(line)}
    operation = fields.get("operation")
    if operation not in OPERATIONS:
        return None
    return {
        "timestamp": line.split(" INFO ", 1)[0].strip(),
        "operation": operation,
        "status_code": fields.get("status_code", ""),
        "duration_ms": fields.get("duration_ms", ""),
        "request_id": fields.get("request_id", ""),
        "trace_id": fields.get("trace_id", ""),
        "outcome": fields.get("outcome", ""),
        "server_version": fields.get("service_version", ""),
        "server_instance": fields.get("service_instance_id", ""),
    }


def read_log(path: Path) -> str:
    """Read a Server log, tolerating the encodings Windows tools produce.

    PowerShell 5.1's ``Tee-Object`` writes UTF-16LE, while the Server itself writes
    UTF-8. Detect the byte-order mark instead of guessing.
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    """Extract the operations and write the evidence file."""
    parser = argparse.ArgumentParser(description="Export StudyFlow memory operations from the Server log.")
    parser.add_argument(
        "--log",
        default=str(Path(__file__).resolve().parents[1] / ".artifacts" / "powercontext-server.log"),
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "evidence" / "powercontext-server-operations.json"),
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"找不到 Server 日志：{log_path}")
        return 1

    records: list[dict[str, str]] = []
    for line in read_log(log_path).splitlines():
        parsed = parse_line(line)
        if parsed:
            records.append(parsed)

    counts: dict[str, int] = {}
    for record in records:
        counts[record["operation"]] = counts.get(record["operation"], 0) + 1

    payload = {
        "source_log": str(log_path),
        "total_operations": len(records),
        "operation_counts": counts,
        "operations": records,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"提取到 {len(records)} 条 PowerContext 操作记录")
    for operation, count in sorted(counts.items()):
        print(f"  {operation:<24}{count}")
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
