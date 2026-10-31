"""``sddctx`` 命令行入口。

调用示例::

    python -m sddctx init
    python -m sddctx import --project minimax/business_project
    python -m sddctx search --query "阶梯满减"
    python -m sddctx context --task "实现阶梯满减" --task-id TASK-001
    python -m sddctx state save --task-id TASK-001 --file state.json
    python -m sddctx state show --task-id TASK-001
    python -m sddctx patch --task-id TASK-001 --task "实现阶梯满减"
    python -m sddctx doctor
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sddctx.config import SddConfig
from sddctx.importer import import_project
from sddctx.llm.coder import SYSTEM_PROMPT, CodeModifier, build_prompt
from sddctx.llm.minimax_client import MiniMaxClient
from sddctx.models import TaskState
from sddctx.retrieval import render_markdown, retrieve
from sddctx.state import PowerContextStateStore
from sddctx.store import build_store


# --- 工具 ---------------------------------------------------------------------


def _load_config(args: argparse.Namespace) -> SddConfig:
    env_file = Path(args.env_file) if getattr(args, "env_file", None) else None
    config = SddConfig.from_env(env_file)
    if getattr(args, "project", None):
        config.project_root = Path(args.project).resolve()
    if getattr(args, "work_dir", None):
        config.work_dir = Path(args.work_dir).resolve()
    return config


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {path}")


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --- 子命令 -------------------------------------------------------------------


async def cmd_init(args: argparse.Namespace) -> int:
    config = _load_config(args)
    store = build_store(config)
    await store.initialize()
    try:
        stats = await store.stats()
        _print_json({"backend": config.db_backend, "initialized": True, "stats": stats})
    finally:
        await store.close()
    return 0


async def cmd_import(args: argparse.Namespace) -> int:
    config = _load_config(args)
    bundle = import_project(config.project_root)
    store = build_store(config)
    await store.initialize()
    try:
        stats = await store.upsert(bundle)
        payload = {
            "backend": config.db_backend,
            "project_root": str(config.project_root),
            "imported": bundle.counts,
            "changes": stats.to_dict(),
        }
        _dump(config.evidence_dir / "import_report.json", payload)
        _print_json(payload)
    finally:
        await store.close()
    return 0


async def cmd_search(args: argparse.Namespace) -> int:
    config = _load_config(args)
    store = build_store(config)
    await store.initialize()
    try:
        hits = await store.search(args.query, limit=args.limit)
        specs = await store.get_specs([h.item_id for h in hits if h.item_kind == "spec"])
        payload = {
            "query": args.query,
            "backend": config.db_backend,
            "hits": [hit.to_dict() for hit in hits],
            "matched_specs": [spec.to_dict() for spec in specs],
        }
        _dump(config.evidence_dir / "search_report.json", payload)
        _print_json(payload)
    finally:
        await store.close()
    return 0


async def cmd_context(args: argparse.Namespace) -> int:
    config = _load_config(args)
    state_store = PowerContextStateStore(config)

    state: TaskState | None = None
    if args.task_id and not args.no_state:
        try:
            state = await state_store.load(args.task_id)
        except Exception as error:  # noqa: BLE001 - 状态读取失败不应阻断上下文生成
            print(f"[警告] 读取 PowerContext 状态失败：{error}", file=sys.stderr)

    store = build_store(config)
    await store.initialize()
    try:
        context = await retrieve(store, task=args.task, task_id=args.task_id or "TASK-UNSPECIFIED", state=state)
    finally:
        await store.close()

    markdown = render_markdown(context)
    _dump(config.evidence_dir / f"context-{context.task_id}.json", context.to_dict())
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"已写入 {path}")
    if args.json:
        _print_json(context.to_dict())
    if not args.json and not args.out:
        print(markdown)
    return 0


async def cmd_prompt(args: argparse.Namespace) -> int:
    """只组装并落盘将要交给 MiniMax 的提示词，不发起模型调用。"""

    config = _load_config(args)
    state_store = PowerContextStateStore(config)
    state: TaskState | None = None
    if args.task_id and not args.no_state:
        try:
            state = await state_store.load(args.task_id)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] 读取 PowerContext 状态失败：{error}", file=sys.stderr)

    store = build_store(config)
    await store.initialize()
    try:
        context = await retrieve(
            store, task=args.task, task_id=args.task_id or "TASK-UNSPECIFIED", state=state
        )
    finally:
        await store.close()

    markdown = render_markdown(context)
    prompt = build_prompt(markdown, task=args.task, extra_instruction=args.instruction or "")
    _dump(config.evidence_dir / f"context-{context.task_id}.json", context.to_dict())
    path = config.evidence_dir / "minimax" / f"minimax-prompt-{context.task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# SYSTEM\n\n{SYSTEM_PROMPT}\n\n# PROMPT\n\n{prompt}\n",
        encoding="utf-8",
    )
    print(f"已写入 {path}")
    return 0


async def cmd_state_init_scope(args: argparse.Namespace) -> int:
    config = _load_config(args)
    store = PowerContextStateStore(config)
    scope_id = await store.ensure_scope()
    print(scope_id)
    return 0


async def cmd_state_save(args: argparse.Namespace) -> int:
    config = _load_config(args)
    state = TaskState.from_dict(json.loads(Path(args.file).read_text(encoding="utf-8")))
    if not state.task_id:
        state.task_id = args.task_id or ""
    if args.task_id:
        state.task_id = args.task_id
    if not state.description:
        state.description = args.task or state.task_id

    store = PowerContextStateStore(config)
    receipt = await store.save(state)
    payload = {"state": state.to_dict(), "receipt": receipt.to_dict()}
    _dump(config.evidence_dir / f"state-save-{state.task_id}.json", payload)
    _print_json({"scope_id": receipt.scope_id, "task_id": state.task_id, "status": state.status})
    return 0


async def cmd_state_show(args: argparse.Namespace) -> int:
    config = _load_config(args)
    store = PowerContextStateStore(config)
    state = await store.load(args.task_id)
    if state is None:
        print(f"未找到任务 {args.task_id} 的状态", file=sys.stderr)
        return 2
    payload = state.to_dict()
    _dump(config.evidence_dir / f"state-read-{args.task_id}.json", payload)
    _print_json(payload)
    return 0


async def cmd_patch(args: argparse.Namespace) -> int:
    config = _load_config(args)
    if not args.response_file:
        config.require_minimax()

    state_store = PowerContextStateStore(config)
    state: TaskState | None = None
    if args.task_id and not args.no_state:
        try:
            state = await state_store.load(args.task_id)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] 读取 PowerContext 状态失败：{error}", file=sys.stderr)

    store = build_store(config)
    await store.initialize()
    try:
        context = await retrieve(
            store,
            task=args.task,
            task_id=args.task_id or "TASK-UNSPECIFIED",
            state=state,
        )
    finally:
        await store.close()

    markdown = render_markdown(context)
    modifier = CodeModifier(MiniMaxClient(config))
    response_file = Path(args.response_file) if args.response_file else None
    result = modifier.generate(
        context_markdown=markdown,
        task=args.task,
        project_root=config.project_root,
        extra_instruction=args.instruction or "",
        dry_run=args.dry_run,
        response_file=response_file,
        response_model=args.response_model,
    )
    payload = result.to_dict()
    _dump(config.evidence_dir / "minimax" / f"minimax-call-{context.task_id}.json", payload)
    if result.call is not None:
        _dump(
            config.evidence_dir / f"minimax-prompt-{context.task_id}.md",
            f"# SYSTEM\n\n{result.call.system}\n\n# PROMPT\n\n{result.call.prompt}\n",
        )
        _dump(
            config.evidence_dir / f"minimax-response-{context.task_id}.md",
            result.call.response_text,
        )
    _print_json(
        {
            "applied": result.applied,
            "changed_files": result.changed_files,
            "model": result.call.model if result.call else None,
            "elapsed_seconds": result.call.elapsed_seconds if result.call else None,
            "log": result.log,
        }
    )
    return 0 if result.applied or args.dry_run else 1


async def cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args)
    report: dict[str, Any] = {
        "project_root": str(config.project_root),
        "db_backend": config.db_backend,
        "seekdb_path": str(config.seekdb_path),
        "oceanbase_dsn_configured": bool(config.oceanbase_dsn),
        "minimax": {
            "base_url": config.minimax_base_url,
            "model": config.minimax_model,
            "protocol": config.minimax_protocol,
            "api_key_configured": bool(config.minimax_api_key),
        },
        "powercontext": {
            "url": config.powercontext_url,
            "scope": config.powercontext_scope,
            "token_configured": bool(config.powercontext_token),
        },
    }

    store = build_store(config)
    try:
        await store.initialize()
        report["database"] = {"ok": True, "stats": await store.stats()}
    except Exception as error:  # noqa: BLE001
        report["database"] = {"ok": False, "error": str(error)}
    finally:
        await store.close()

    try:
        info = await PowerContextStateStore(config).ping()
        report["powercontext"]["reachable"] = True
        report["powercontext"]["server_info"] = info
    except Exception as error:  # noqa: BLE001
        report["powercontext"]["reachable"] = False
        report["powercontext"]["error"] = str(error)

    _print_json(report)
    ok = bool(report["database"].get("ok")) and bool(report["powercontext"].get("reachable"))
    return 0 if ok else 1


# --- 入口 ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description="面向软件持续演进的 SDD 上下文管理模块")
    parser.add_argument("--env-file", default=None, help="可选的 .env 文件路径")
    parser.add_argument("--project", default=None, help="业务工程根目录")
    parser.add_argument("--work-dir", default=None, help="运行目录（存放证据文件）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化资料库").set_defaults(handler=cmd_init)
    sub.add_parser("import", help="导入规格、代码与测试").set_defaults(handler=cmd_import)

    search = sub.add_parser("search", help="检索资料库")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(handler=cmd_search)

    context = sub.add_parser("context", help="生成开发上下文")
    context.add_argument("--task", required=True)
    context.add_argument("--task-id", default=None)
    context.add_argument("--json", action="store_true")
    context.add_argument("--out", default=None, help="把 Markdown 上下文写入文件")
    context.add_argument("--no-state", action="store_true", help="不读取 PowerContext 中保存的状态")
    context.set_defaults(handler=cmd_context)

    state = sub.add_parser("state", help="任务状态（保存与读取）")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_sub.add_parser("init-scope", help="创建并打印 PowerContext Scope").set_defaults(
        handler=cmd_state_init_scope
    )
    state_save = state_sub.add_parser("save", help="保存任务状态")
    state_save.add_argument("--task-id", default=None)
    state_save.add_argument("--task", default=None)
    state_save.add_argument("--file", required=True, help="状态 JSON 文件")
    state_save.set_defaults(handler=cmd_state_save)
    state_show = state_sub.add_parser("show", help="读取任务状态")
    state_show.add_argument("--task-id", required=True)
    state_show.set_defaults(handler=cmd_state_show)

    patch = sub.add_parser("patch", help="调用 MiniMax 完成代码修改")
    patch.add_argument("--task", required=True)
    patch.add_argument("--task-id", default=None)
    patch.add_argument("--instruction", default=None, help="追加给模型的额外要求")
    patch.add_argument("--dry-run", action="store_true", help="只生成补丁，不写入文件")
    patch.add_argument("--no-state", action="store_true")
    patch.add_argument(
        "--response-file",
        default=None,
        help="使用已留档的模型响应复现应用路径（不发起实时 API 调用）",
    )
    patch.add_argument(
        "--response-model",
        default=None,
        help="记录已留档响应由哪个模型产出（配合 --response-file 使用）",
    )
    patch.set_defaults(handler=cmd_patch)

    prompt = sub.add_parser("prompt", help="只组装并落盘交给 MiniMax 的提示词")
    prompt.add_argument("--task", required=True)
    prompt.add_argument("--task-id", default=None)
    prompt.add_argument("--instruction", default=None)
    prompt.add_argument("--no-state", action="store_true")
    prompt.set_defaults(handler=cmd_prompt)

    sub.add_parser("doctor", help="检查依赖与配置").set_defaults(handler=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
