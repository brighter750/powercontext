#!/usr/bin/env python3
"""Validate a Dify Chatflow DSL before importing it into Dify.

The checks cover the mistakes that actually break an import or a run: unknown variable
references, edges that point nowhere, nodes that cannot be reached, code nodes that do not
compile or whose declared outputs do not match the returned keys, HTTP nodes whose body is
not valid JSON once the templates are filled in, and if-else branches without a matching
edge.

Usage:
    python check_dsl.py dify/apps/innkeeper-chatflow.yml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

VARIABLE_PATTERN = re.compile(r"\{\{#([^#{}]+)#?\}\}")
FORBIDDEN_TECHNOLOGIES = ("seekdb",)

REQUIRED_NODE_FIELDS: dict[str, tuple[str, ...]] = {
    "start": ("title", "type", "variables"),
    "code": ("title", "type", "code", "code_language", "outputs", "variables"),
    "http-request": ("title", "type", "method", "url", "headers", "body", "timeout"),
    "llm": ("title", "type", "model", "prompt_template"),
    "if-else": ("title", "type", "cases"),
    "answer": ("title", "type", "answer"),
}

SYSTEM_SELECTORS = {"sys.query", "sys.user_id", "sys.files", "sys.conversation_id"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[str] = []

    def ok(self, message: str) -> None:
        self.checks.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def collect_selectors(text: str) -> list[str]:
    return [match.group(1).strip() for match in VARIABLE_PATTERN.finditer(text or "")]


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 Dify Chatflow DSL")
    parser.add_argument("dsl", nargs="?", default=str(Path(__file__).resolve().parents[1] / "apps" / "innkeeper-chatflow.yml"))
    args = parser.parse_args()

    path = Path(args.dsl)
    report = Report()
    raw = path.read_text(encoding="utf-8")

    for technology in FORBIDDEN_TECHNOLOGIES:
        if technology in raw.lower():
            report.error(f"DSL 里出现了不应引入的技术：{technology}")

    document = yaml.safe_load(raw)
    for key in ("app", "kind", "version", "workflow"):
        if key not in document:
            report.error(f"缺少顶层字段 {key}")
    if document.get("kind") != "app":
        report.error(f"kind 应该是 app，实际是 {document.get('kind')!r}")
    if document.get("version") != "0.1.5":
        report.warn(f"DSL version 是 {document.get('version')!r}，Dify 1.x 导出通常是 0.1.5")
    app = document.get("app", {})
    if app.get("mode") != "advanced-chat":
        report.error(f"app.mode 应该是 advanced-chat，实际是 {app.get('mode')!r}")
    report.ok(f"应用：{app.get('name')}（mode={app.get('mode')}, version={document.get('version')}）")

    workflow = document.get("workflow", {})
    graph = workflow.get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        report.error("workflow.graph.nodes 为空")
        return finish(report)

    node_ids: list[str] = []
    node_by_id: dict[str, dict] = {}
    for node in nodes:
        node_id = node.get("id")
        data = node.get("data", {})
        node_type = data.get("type")
        if not node_id:
            report.error(f"存在没有 id 的节点：{data.get('title')}")
            continue
        if node_id in node_by_id:
            report.error(f"节点 id 重复：{node_id}")
        node_ids.append(node_id)
        node_by_id[node_id] = node
        for field in REQUIRED_NODE_FIELDS.get(node_type, ("title", "type")):
            if field not in data:
                report.error(f"节点 {node_id}（{node_type}）缺少字段 {field}")
        for meta in ("position", "positionAbsolute", "width", "height", "sourcePosition", "targetPosition"):
            if meta not in node:
                report.error(f"节点 {node_id} 缺少图元字段 {meta}")
    report.ok(f"节点 {len(nodes)} 个，边 {len(edges)} 条")

    for edge in edges:
        for endpoint in ("source", "target"):
            if edge.get(endpoint) not in node_by_id:
                report.error(f"边 {edge.get('id')} 的 {endpoint}={edge.get(endpoint)!r} 没有对应节点")
        for meta in ("id", "sourceHandle", "targetHandle", "type"):
            if meta not in edge:
                report.error(f"边 {edge.get('id')} 缺少字段 {meta}")

    starts = [node for node in nodes if node["data"].get("type") == "start"]
    answers = [node for node in nodes if node["data"].get("type") == "answer"]
    if len(starts) != 1:
        report.error(f"应该恰好有一个 start 节点，实际 {len(starts)} 个")
    if len(answers) != 1:
        report.error(f"应该恰好有一个 answer 节点，实际 {len(answers)} 个")

    reachable: set[str] = set()
    if starts:
        stack = [starts[0]["id"]]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for edge in edges:
                if edge.get("source") == current:
                    stack.append(edge["target"])
    unreachable = sorted(set(node_ids) - reachable)
    if unreachable:
        report.error(f"这些节点从 start 走不到：{unreachable}")

    environment = {variable["name"] for variable in workflow.get("environment_variables", [])}
    known_outputs: set[str] = set(SYSTEM_SELECTORS)
    for node in nodes:
        data = node.get("data", {})
        node_type = data.get("type")
        node_id = node["id"]
        if node_type == "code":
            for output in data.get("outputs", {}):
                known_outputs.add(f"{node_id}.{output}")
        elif node_type == "http-request":
            known_outputs.update({f"{node_id}.body", f"{node_id}.status_code", f"{node_id}.headers", f"{node_id}.files"})
        elif node_type == "llm":
            known_outputs.add(f"{node_id}.text")
        elif node_type == "if-else":
            known_outputs.add(f"{node_id}.result")
        elif node_type == "start":
            for variable in data.get("variables", []):
                known_outputs.add(f"{node_id}.{variable['variable']}")

    def check_selectors(text: str, where: str) -> None:
        for selector in collect_selectors(text):
            if selector.startswith("env."):
                if selector[4:] not in environment:
                    report.error(f"{where} 引用了未声明的环境变量 {selector}")
                continue
            if selector not in known_outputs:
                report.error(f"{where} 引用了未知变量 {selector}")

    for node in nodes:
        data = node.get("data", {})
        node_type = data.get("type")
        node_id = node["id"]
        if node_type == "code":
            code = data.get("code", "")
            try:
                compiled = compile(code, f"{node_id}.py", "exec")
            except SyntaxError as error:
                report.error(f"代码节点 {node_id} 语法错误：{error}")
                continue
            namespace: dict[str, object] = {}
            exec(compiled, namespace)  # noqa: S102
            main_function = namespace.get("main")
            if not callable(main_function):
                report.error(f"代码节点 {node_id} 没有可调用的 main()")
                continue
            declared = {variable["variable"] for variable in data.get("variables", [])}
            for variable in data.get("variables", []):
                selector = variable["value_selector"]
                if len(selector) != 2:
                    report.error(f"代码节点 {node_id} 的变量选择器格式不对：{selector}")
                    continue
                check_selectors(f"{{{{#{selector[0]}.{selector[1]}#}}}}", f"代码节点 {node_id}")
            parameters = set(main_function.__code__.co_varnames[: main_function.__code__.co_argcount])
            missing = declared - parameters
            if missing:
                report.error(f"代码节点 {node_id} 的 main() 缺少形参：{sorted(missing)}")
            unknown = parameters - declared
            if unknown:
                report.error(f"代码节点 {node_id} 声明了未接线的形参：{sorted(unknown)}")
            for output in data.get("outputs", {}):
                if not isinstance(output, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", output):
                    report.error(f"代码节点 {node_id} 的输出名不合法：{output!r}")
        elif node_type == "http-request":
            check_selectors(data.get("url", ""), f"HTTP 节点 {node_id} 的 url")
            check_selectors(data.get("headers", ""), f"HTTP 节点 {node_id} 的 headers")
            body = data.get("body") or {}
            body_data = body.get("data", "")
            check_selectors(body_data, f"HTTP 节点 {node_id} 的 body")
            if body.get("type") == "json":
                rendered = VARIABLE_PATTERN.sub("null", body_data)
                try:
                    json.loads(rendered)
                except json.JSONDecodeError as error:
                    report.error(f"HTTP 节点 {node_id} 的 body 不是合法 JSON：{error}")
            if not data.get("url", "").startswith("{{#env.POWERCONTEXT_BASE_URL#}}"):
                report.warn(f"HTTP 节点 {node_id} 的 url 没有走 POWERCONTEXT_BASE_URL 环境变量")
        elif node_type == "llm":
            model = data.get("model", {})
            if not model.get("provider") or not model.get("name"):
                report.error(f"LLM 节点 {node_id} 没有指定 provider/name")
            for item in data.get("prompt_template", []):
                check_selectors(item.get("text", ""), f"LLM 节点 {node_id} 的提示词")
            if not data.get("prompt_template"):
                report.error(f"LLM 节点 {node_id} 没有提示词")
        elif node_type == "if-else":
            handles = {edge.get("sourceHandle") for edge in edges if edge.get("source") == node_id}
            for case in data.get("cases", []):
                if case.get("id") not in handles:
                    report.error(f"if-else 节点 {node_id} 的分支 {case.get('id')} 没有对应的出边")
                for condition in case.get("conditions", []):
                    selector = condition.get("variable_selector", [])
                    if len(selector) != 2:
                        report.error(f"if-else 节点 {node_id} 的条件选择器格式不对：{selector}")
                    else:
                        known_outputs.add(f"{selector[0]}.{selector[1]}")
        elif node_type == "answer":
            check_selectors(data.get("answer", ""), f"回答节点 {node_id}")

    branch_nodes = [node for node in nodes if node["data"].get("type") == "if-else"]
    for node in branch_nodes:
        node_id = node["id"]
        for case in node["data"].get("cases", []):
            for condition in case.get("conditions", []):
                selector = condition.get("variable_selector", [])
                if len(selector) == 2 and f"{selector[0]}.{selector[1]}" not in known_outputs:
                    report.error(f"if-else 节点 {node_id} 的条件引用了未知变量 {selector[0]}.{selector[1]}")

    report.ok("变量引用、代码节点、HTTP 请求体、条件分支均已检查")
    return finish(report)


def finish(report: Report) -> int:
    for message in report.checks:
        print(f"[OK] {message}")
    for message in report.warnings:
        print(f"[WARN] {message}")
    for message in report.errors:
        print(f"[ERROR] {message}")
    if report.errors:
        print(f"\n校验失败：{len(report.errors)} 个错误")
        return 1
    print(f"\n校验通过（{len(report.checks)} 项检查，{len(report.warnings)} 个提醒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
