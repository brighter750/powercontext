"""导出资料库证据：引擎版本、建表 DDL、表计数与一次真实检索查询。

用法::

    python minimax/scripts/dump_database_evidence.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_DIR))

from sddctx.config import SddConfig  # noqa: E402
from sddctx.store import build_store  # noqa: E402
from sddctx.store.mysql_like import CODE_KIND, SPEC_KIND, TEST_KIND  # noqa: E402
from sddctx.tokenizer import tokenize  # noqa: E402

TASK = (
    "实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，"
    "只取最高一档，不叠加；会员折扣先于阶梯满减计算。"
)


async def main_async(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    env["SDDCTX_PROJECT_ROOT"] = str(MINIMAX_DIR / "business_project")
    env["SDDCTX_WORK_DIR"] = str(args.work_dir)
    env["SDDCTX_SEEKDB_PATH"] = str(Path(args.work_dir) / "seekdb")
    config = SddConfig.from_env()

    store = build_store(config)
    await store.initialize()
    out: list[str] = []
    out.append("# 资料库使用记录")
    out.append("")
    out.append(f"- 执行时间(UTC)：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out.append(f"- 后端类型：`{config.db_backend}`")
    out.append("")

    try:
        version_rows = await store._query("SELECT VERSION() AS version")  # noqa: SLF001
        out.append("## 1. 数据库类型与版本")
        out.append("")
        out.append("```sql")
        out.append("SELECT VERSION();")
        out.append("```")
        out.append("")
        out.append(f"```text\n{version_rows[0]['version']}\n```")
        out.append("")

        out.append("## 2. 表结构（DDL）")
        out.append("")
        for table in (store.specs_table, store.code_table, store.tests_table, store.token_table):
            rows = await store._query(f"SHOW CREATE TABLE {table}")
            ddl = ""
            if rows:
                row = rows[0]
                for key in ("Create Table", "Create View", "create table"):
                    if key in row:
                        ddl = str(row[key])
                        break
                else:
                    ddl = str(next(iter(row.values())))
            out.append(f"### `{table}`")
            out.append("")
            out.append("```sql")
            out.append(ddl.strip())
            out.append("```")
            out.append("")

        out.append("## 3. 表内数据量")
        out.append("")
        out.append("| 表 | 行数 |")
        out.append("| --- | --- |")
        stats = await store.stats()
        for key, table in (
            ("specs", store.specs_table),
            ("code_symbols", store.code_table),
            ("tests", store.tests_table),
            ("tokens", store.token_table),
        ):
            out.append(f"| `{table}` | {stats[key]} |")
        out.append("")

        out.append("## 4. 规格条目的修订版本")
        out.append("")
        out.append("| 规格编号 | revision | content_hash | updated_at |")
        out.append("| --- | --- | --- | --- |")
        for row in stats["spec_revisions"]:
            out.append(
                f"| `{row['spec_id']}` | {row['revision']} | `{row['content_hash'][:16]}…` "
                f"| {row['updated_at']} |"
            )
        out.append("")

        tokens = sorted({token for token in tokenize(TASK) if token})
        token_list = ", ".join(f"'{token}'" for token in tokens)
        out.append("## 5. 实际检索查询（上下文即由这次查询的结果组装）")
        out.append("")
        out.append(f"任务描述：{TASK}")
        out.append("")
        out.append(f"分词结果（{len(tokens)} 个）：`{'`, `'.join(tokens)}`")
        out.append("")
        out.append("```sql")
        out.append(
            "SELECT item_kind, item_id, SUM(tf) AS score\n"
            f"FROM {store.token_table}\n"
            f"WHERE token IN ({token_list})\n"
            "GROUP BY item_kind, item_id\n"
            "ORDER BY score DESC, item_kind ASC, item_id ASC\n"
            "LIMIT 20;"
        )
        out.append("```")
        out.append("")
        hits = await store.search(TASK, limit=20)
        out.append("| 类型 | 条目 | 词频得分 |")
        out.append("| --- | --- | --- |")
        for hit in hits:
            out.append(f"| {hit.item_kind} | `{hit.item_id}` | {hit.score} |")
        out.append("")

        out.append("## 6. 检索结果如何被上下文使用")
        out.append("")
        out.append("| 命中类型 | 命中条目 | 在上下文中的位置 |")
        out.append("| --- | --- | --- |")
        spec_ids = [hit.item_id for hit in hits if hit.item_kind == SPEC_KIND][:6]
        code_ids = [hit.item_id for hit in hits if hit.item_kind == CODE_KIND][:6]
        test_ids = [hit.item_id for hit in hits if hit.item_kind == TEST_KIND][:6]
        for spec_id in spec_ids:
            spec = await store.get_spec(spec_id)
            out.append(
                f"| spec | `{spec_id}` | “适用的规格条目” → `{spec.source_path}:{spec.source_line}`"
                f"（revision={spec.revision}） |"
            )
        for item_id in code_ids:
            symbols = await store.get_code_symbols_by_ids([item_id])
            for symbol in symbols:
                out.append(
                    f"| code | `{item_id}` | “需要修改的代码文件或符号” → "
                    f"`{symbol.file_path}:{symbol.line}` `{symbol.symbol}` |"
                )
        for item_id in test_ids:
            cases = await store.get_tests_by_ids([item_id])
            for case in cases:
                out.append(
                    f"| test | `{item_id}` | “用于验证功能与兼容性的测试” → "
                    f"`{case.file_path}:{case.line}` `{case.test_name}` |"
                )
        out.append("")

        out.append("## 7. 规格 → 代码 与 规格 → 测试 的关联查询")
        out.append("")
        out.append("```sql")
        out.append(
            "SELECT symbol, file_path, line, kind FROM "
            f"{store.code_table} WHERE symbol IN ('tiered_discount_rate', 'apply_discounts');"
        )
        out.append("```")
        out.append("")
        symbols = await store.get_code_symbols_by_names(["tiered_discount_rate", "apply_discounts"])
        for symbol in symbols:
            out.append(f"- `{symbol.file_path}:{symbol.line}` `{symbol.symbol}` — `{symbol.signature}`")
        out.append("")
        out.append("```sql")
        out.append(
            "SELECT test_name, file_path, line, requirement_ids FROM "
            f"{store.tests_table} WHERE requirement_ids LIKE '%SPEC-PRICING-102%' ORDER BY line;"
        )
        out.append("```")
        out.append("")
        linked = await store.get_tests_for_specs(["SPEC-PRICING-102"])
        for case in linked:
            out.append(
                f"- `{case.file_path}:{case.line}` `{case.test_name}` "
                f"← 覆盖 {', '.join(case.requirement_ids)}"
            )
        out.append("")
    finally:
        await store.close()

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"已写入 {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="导出资料库证据")
    parser.add_argument("--work-dir", default=str(MINIMAX_DIR / ".sddctx-run"))
    parser.add_argument(
        "--out", default=str(MINIMAX_DIR / "evidence" / "db" / "database-usage.md")
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
