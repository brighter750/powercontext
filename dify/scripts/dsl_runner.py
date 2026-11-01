#!/usr/bin/env python3
"""Execute a Dify Chatflow DSL locally, node by node, against a real PowerContext Server.

The interpreter reads the exported DSL and runs exactly the nodes, URLs, request bodies and
prompts that the DSL declares, so the recorded evidence cannot drift away from the workflow
that reviewers import into Dify.

Node types supported: start, code, http-request, llm, if-else, answer.

Usage:
    python dsl_runner.py --dsl dify/apps/innkeeper-chatflow.yml --player-id afu --message "..."
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml

VARIABLE_PATTERN = re.compile(r"\{\{#([^#{}]+)#?\}\}")

REPO_ROOT = Path(__file__).resolve().parents[2]


class RunnerError(RuntimeError):
    """Raised when the workflow cannot continue."""


def render_template(text: str, context: dict[str, Any]) -> str:
    """Resolve Dify ``{{#node.variable#}}`` references inside a string."""

    def replace(match: re.Match[str]) -> str:
        selector = match.group(1).strip()
        if selector not in context:
            raise RunnerError(f"模板引用了未知变量：{selector}")
        value = context[selector]
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return VARIABLE_PATTERN.sub(replace, text or "")


def render_headers(raw: str, context: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = render_template(value.strip(), context)
    return headers


class DslRunner:
    """A minimal Dify workflow interpreter for the node types this project uses."""

    def __init__(
        self,
        dsl_path: Path,
        powercontext_base_url: str,
        powercontext_token: str,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        llm_protocol: str = "anthropic",
        llm_timeout: float = 180.0,
        llm_auth_style: str = "bearer",
    ) -> None:
        self.dsl_path = dsl_path
        self.document = yaml.safe_load(dsl_path.read_text(encoding="utf-8"))
        self.graph = self.document["workflow"]["graph"]
        self.nodes = {node["id"]: node for node in self.graph["nodes"]}
        self.edges = self.graph["edges"]
        self.powercontext_base_url = powercontext_base_url.rstrip("/")
        self.powercontext_token = powercontext_token
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_protocol = llm_protocol
        self.llm_timeout = llm_timeout
        self.llm_auth_style = llm_auth_style
        self.context: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []
        self.environment: dict[str, str] = {
            variable["name"]: str(variable.get("value") or "")
            for variable in self.document["workflow"].get("environment_variables", [])
        }
        self.environment["POWERCONTEXT_BASE_URL"] = self.powercontext_base_url
        self.environment["POWERCONTEXT_TOKEN"] = self.powercontext_token
        self.client = httpx.Client(timeout=60.0)

    # ------------------------------------------------------------------ helpers
    def _outgoing(self, node_id: str) -> list[dict[str, Any]]:
        return [edge for edge in self.edges if edge["source"] == node_id]

    def _record(self, node_id: str, payload: dict[str, Any]) -> None:
        self.trace.append({"node": node_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **payload})

    # ------------------------------------------------------------------- nodes
    def _run_start(self, node: dict[str, Any], inputs: dict[str, Any], query: str) -> None:
        self.context["sys.query"] = query
        for variable in node["data"].get("variables", []):
            name = variable["variable"]
            self.context[f"{node['id']}.{name}"] = inputs.get(name, "")
        self._record(node["id"], {"type": "start", "inputs": inputs, "query": query})

    def _run_code(self, node: dict[str, Any]) -> None:
        data = node["data"]
        namespace: dict[str, Any] = {"__name__": f"dify_node_{node['id']}"}
        exec(compile(data["code"], f"{node['id']}.py", "exec"), namespace)  # noqa: S102
        kwargs: dict[str, Any] = {}
        for variable in data.get("variables", []):
            selector = variable["value_selector"]
            key = f"{selector[0]}.{selector[1]}"
            kwargs[variable["variable"]] = self.context.get(key, "")
        result = namespace["main"](**kwargs)
        for name, value in result.items():
            self.context[f"{node['id']}.{name}"] = value
        self._record(node["id"], {"type": "code", "inputs": kwargs, "outputs": result})

    def _run_http(self, node: dict[str, Any]) -> None:
        data = node["data"]
        url = render_template(data["url"], self.context)
        headers = render_headers(data.get("headers", ""), self.context)
        body_data = ""
        if data.get("body"):
            body_data = render_template(data["body"].get("data", ""), self.context)
        request_kwargs: dict[str, Any] = {"headers": headers}
        if body_data:
            if data["body"].get("type") == "json":
                request_kwargs["content"] = body_data.encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            else:
                request_kwargs["content"] = body_data.encode("utf-8")
        started = time.perf_counter()
        response = self.client.request(
            data.get("method", "get").upper(),
            url,
            **request_kwargs,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        body_text = response.text
        self.context[f"{node['id']}.body"] = body_text
        self.context[f"{node['id']}.status_code"] = response.status_code
        self.context[f"{node['id']}.headers"] = dict(response.headers)
        self.context[f"{node['id']}.files"] = []
        safe_headers = {key: value for key, value in headers.items() if key.lower() != "authorization"}
        self._record(
            node["id"],
            {
                "type": "http-request",
                "request": {
                    "method": data.get("method", "get").upper(),
                    "url": url,
                    "headers": safe_headers,
                    "body": body_data,
                },
                "response": {
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "body": body_text,
                },
            },
        )
        if response.status_code >= 400:
            raise RunnerError(f"{node['id']} 请求失败 HTTP {response.status_code}: {body_text[:400]}")

    def _run_llm(self, node: dict[str, Any]) -> None:
        data = node["data"]
        template = data["prompt_template"]
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for item in template:
            text = render_template(item["text"], self.context)
            if item["role"] == "system":
                system_parts.append(text)
            else:
                messages.append({"role": item["role"], "content": text})
        model_config = data["model"]
        temperature = model_config.get("completion_params", {}).get("temperature", 0.7)
        if self.llm_protocol == "anthropic":
            payload = {
                "model": self.llm_model,
                "max_tokens": 2048,
                "temperature": temperature,
                "system": "\n\n".join(system_parts),
                "messages": messages,
            }
            headers = {"Content-Type": "application/json"}
            url = f"{self.llm_base_url}/messages"
        else:
            payload = {
                "model": self.llm_model,
                "temperature": temperature,
                "messages": [{"role": "system", "content": "\n\n".join(system_parts)}, *messages],
            }
            headers = {"Content-Type": "application/json"}
            url = f"{self.llm_base_url}/chat/completions"
        if self.llm_auth_style in {"bearer", "both"}:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"
        if self.llm_auth_style in {"x-api-key", "both"}:
            headers["x-api-key"] = self.llm_api_key
        started = time.perf_counter()
        response = httpx.post(url, headers=headers, json=payload, timeout=self.llm_timeout)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        if response.status_code != 200:
            raise RunnerError(f"模型调用失败 HTTP {response.status_code}: {response.text[:400]}")
        body = response.json()
        if self.llm_protocol == "anthropic":
            text = "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
        else:
            text = body["choices"][0]["message"]["content"]
        self.context[f"{node['id']}.text"] = text
        self._record(
            node["id"],
            {
                "type": "llm",
                "model": {"provider": model_config.get("provider"), "name": model_config.get("name")},
                "request": {
                    "url": url,
                    "protocol": self.llm_protocol,
                    "model": self.llm_model,
                    "temperature": temperature,
                    "system": "\n\n".join(system_parts),
                    "messages": messages,
                },
                "response": {"elapsed_ms": elapsed_ms, "text": text},
            },
        )

    def _evaluate_condition(self, condition: dict[str, Any]) -> bool:
        selector = condition["variable_selector"]
        left = self.context.get(f"{selector[0]}.{selector[1]}", "")
        operator = condition["comparison_operator"]
        right = condition.get("value", "")
        if operator == "is":
            return str(left) == str(right)
        if operator == "is not":
            return str(left) != str(right)
        if operator == "empty":
            return not str(left).strip()
        if operator == "not empty":
            return bool(str(left).strip())
        if operator == "contains":
            return str(right) in str(left)
        if operator in {">", ">=", "<", "<="}:
            try:
                left_number, right_number = float(left), float(right)
            except (TypeError, ValueError):
                return False
            return {
                ">": left_number > right_number,
                ">=": left_number >= right_number,
                "<": left_number < right_number,
                "<=": left_number <= right_number,
            }[operator]
        raise RunnerError(f"不支持的条件运算符：{operator}")

    def _run_if_else(self, node: dict[str, Any]) -> str:
        data = node["data"]
        result = data["cases"][-1]["id"]
        for case in data["cases"]:
            conditions = case.get("conditions", [])
            if all(self._evaluate_condition(condition) for condition in conditions):
                result = case["id"]
                break
        self.context[f"{node['id']}.result"] = result
        self._record(node["id"], {"type": "if-else", "result": result, "cases": [case["id"] for case in data["cases"]]})
        return result

    # -------------------------------------------------------------------- main
    def run(self, inputs: dict[str, Any], query: str) -> dict[str, Any]:
        self.context = {f"env.{key}": value for key, value in self.environment.items()}
        self.trace = []
        current = "start"
        answer_text = ""
        guard = 0
        while current:
            guard += 1
            if guard > 100:
                raise RunnerError("工作流执行超过 100 步，疑似成环")
            node = self.nodes[current]
            node_type = node["data"]["type"]
            if node_type == "start":
                self._run_start(node, inputs, query)
            elif node_type == "code":
                self._run_code(node)
            elif node_type == "http-request":
                self._run_http(node)
            elif node_type == "llm":
                self._run_llm(node)
            elif node_type == "if-else":
                self._run_if_else(node)
            elif node_type == "answer":
                answer_text = render_template(node["data"].get("answer", ""), self.context)
                self._record(node["id"], {"type": "answer", "answer": answer_text})
                break
            else:
                raise RunnerError(f"不支持的节点类型：{node_type}")

            outgoing = self._outgoing(current)
            if not outgoing:
                break
            if node_type == "if-else":
                chosen = self.context[f"{current}.result"]
                edge = next((item for item in outgoing if item.get("sourceHandle") == chosen), None)
                if edge is None:
                    raise RunnerError(f"if-else 分支 {chosen} 没有对应的出边")
            else:
                edge = outgoing[0]
            current = edge["target"]
        return {"answer": answer_text, "trace": self.trace, "context": self.context}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在本地按 DSL 节点顺序执行 Dify Chatflow")
    parser.add_argument("--dsl", default=str(REPO_ROOT / "dify" / "apps" / "innkeeper-chatflow.yml"))
    parser.add_argument("--player-id", required=True, help="旅人代号")
    parser.add_argument("--message", required=True, help="旅人这一轮说的话")
    parser.add_argument("--trace", help="把完整执行轨迹写入该 JSON 文件")
    parser.add_argument("--powercontext-base-url", default=os.environ.get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--powercontext-token", default=os.environ.get("POWERCONTEXT_TOKEN", "disabled"))
    parser.add_argument("--llm-base-url", default=os.environ.get("INNKEPER_LLM_BASE_URL", ""))
    parser.add_argument("--llm-api-key", default=os.environ.get("INNKEPER_LLM_API_KEY", ""))
    parser.add_argument("--llm-model", default=os.environ.get("INNKEPER_LLM_MODEL", "MiniMax-M3.1-Flash"))
    parser.add_argument("--llm-protocol", default=os.environ.get("INNKEPER_LLM_PROTOCOL", "anthropic"))
    parser.add_argument(
        "--llm-auth-style",
        default=os.environ.get("INNKEPER_LLM_AUTH_STYLE", "bearer"),
        choices=("bearer", "x-api-key", "both"),
        help="模型服务鉴权头风格：bearer 用 Authorization，x-api-key 用 x-api-key",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.llm_base_url or not args.llm_api_key:
        print("缺少模型服务配置：请设置 INNKEPER_LLM_BASE_URL 与 INNKEPER_LLM_API_KEY", file=sys.stderr)
        return 2
    runner = DslRunner(
        dsl_path=Path(args.dsl),
        powercontext_base_url=args.powercontext_base_url,
        powercontext_token=args.powercontext_token,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        llm_model=args.llm_model,
        llm_protocol=args.llm_protocol,
        llm_auth_style=args.llm_auth_style,
    )
    result = runner.run({"player_id": args.player_id}, args.message)
    print(result["answer"])
    if args.trace:
        payload = {
            "dsl": str(args.dsl),
            "player_id": args.player_id,
            "message": args.message,
            "answer": result["answer"],
            "trace": result["trace"],
        }
        target = Path(args.trace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[trace] {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunnerError as error:
        print(f"运行失败：{error}", file=sys.stderr)
        raise SystemExit(1) from error
