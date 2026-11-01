# 验证证据

本目录是《忘忧酒肆 · 记忆掌柜》在**真实 PowerContext Server** 上跑出来的运行记录，
不是示意图或构造数据。

## 这组证据证明了什么

| 结论 | 证据 |
| --- | --- |
| PowerContext 真的存下了应用记忆 | `session-1-turn-1.json` 里 `remember` 节点的响应体含真实 `artifact_id`（`art_...`）；`powercontext-server.log` 里同一次操作有服务端 `request_id` |
| 记忆能跨会话被读回并用上 | `session-2-turn-2.json` 的提示词里带的是从 PowerContext 读回的册子摘要；该轮提问不含"桂花酿"，回答里却出现了 |
| 记忆会随互动更新 | `session-3-turn-3.json` 写入新条目；`verification-summary.json` 里册子条目数 1 → 1 → 2 |
| 记忆按旅人代号隔离 | `control-other-player.json`：另一个代号拿到的是空册子，掌柜说"没记过您爱喝什么酒" |
| 同一件事不会被重复记录 | 会话 2（纯回忆轮）条目数保持 1 条 |

## 文件

| 文件 | 说明 |
| --- | --- |
| `verification-summary.json` | 五项检查的结论、三次会话后的册子快照、对照组回答、使用的 scope id |
| `session-1-turn-1.json` | 会话 1 完整轨迹（13 个节点逐个记录）：HTTP 请求体/响应体、模型提示词与回复、Code 节点输入输出 |
| `session-2-turn-2.json` | 会话 2 完整轨迹（全新进程） |
| `session-3-turn-3.json` | 会话 3 完整轨迹（全新进程，写入新记忆） |
| `control-other-player.json` | 反向对照轨迹（另一个旅人代号） |
| `powercontext-server-log.txt` | 从 PowerContext Server 自己的 JSON 日志中筛出的记忆/Scope 操作记录 |

## 怎么读这些轨迹

轨迹是一个 JSON 数组，每个元素对应 DSL 里的一个节点：

- `type: "http-request"` —— `request` 是真实发出的 URL、请求体，`response` 是服务端真实返回的状态码与响应体；
- `type: "llm"` —— `request.system` 是渲染后的完整提示词（其中【他的记忆册】一段就是 PowerContext 读回来的内容），
  `response.text` 是模型原话；
- `type: "code"` —— 节点的输入与输出，可与 DSL 里的代码逐行对照；
- `type: "if-else"` —— 本轮走的是哪个分支。

对照关系：

```
session-2-turn-2.json  →  llm.request.system 中的【他的记忆册（由 PowerContext 读回，可能为空）】
                       ↕  一致
session-2-turn-2.json  →  load_memory.response.body（POST /v1/memory/entries/list 的真实响应）
                       ↕  同一次操作
powercontext-server-log.txt  →  operation=list_memory_entries status=200 request_id=...
```

## 复现

见 `dify/README.md` 第 9、10 节。整套验证脚本：

```bash
python dify/scripts/verify_cross_session.py --player-id "阿福" --evidence-dir dify/evidence/cross-session
```

每轮都是**新起的子进程**，所以跨会话还能记住的唯一来源就是 PowerContext 本身。

## 环境说明

- PowerContext Server：本仓库源码，`POWERCONTEXT_SERVER_DATABASE_URL` 指向独立 SQLite 库，监听 `127.0.0.1:8000`，未开启鉴权。
- 模型：MiniMax 系列（本地验证用 `MiniMax-M3.1-Flash`）。DSL 里写的是 `MiniMax-M2.7` + `langgenius/openai_api_compatible`，
  评审按 `dify/README.md` 第 5.3 节配置成自己的模型服务即可。
- 本机没有 Docker，Dify 容器栈未在提交方机器上启动过；已完成的验证见 `dify/README.md` 第 12 节。
