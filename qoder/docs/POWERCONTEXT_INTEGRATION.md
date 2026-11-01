# PowerContext 接入说明

本文记录 StudyFlow **实际使用**的 PowerContext 接口、认证方式、用户标识方案、
写入/检索/更新方式与持久化验证过程。所有接口均以本仓库内的
`src/powercontext/http/_generated/operations.py` 与 `openapi/powercontext.yaml`
为准，并在真实运行的 Server（HTTP API 契约 v1.4）上逐条验证过。

---

## 1. 部署方式

### 1.1 版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| PowerContext Server | 1.4.x（契约 v1.4） | 本地 SQLite 持久化 |
| HTTP API 契约 | `api_contract_version = 1.4` | 由 `GET /v1/server-info` 返回 |
| 接入方式 | HTTP API | 亦支持 MCP / Python SDK；本项目选择 HTTP API |

### 1.2 安装

```bash
# 方式一：安装已发布版本
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext --version

# 方式二：用本仓库源码（开发调试更方便）
uv sync
uv run powercontext server run
```

> `powercontext[cli,server]` 不含 seekDB 扩展，本方案也**不需要** seekDB。

### 1.3 启动参数

StudyFlow 使用下面这组环境变量启动一个**独立**的 Server 实例，
避免和本机其它演示互相干扰：

```bash
export POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
export POWERCONTEXT_SERVER_HTTP_PORT=8765
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/qoder/.artifacts/powercontext-studyflow.db"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="<与客户端一致的令牌>"
export POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
powercontext server run
```

Windows 下可直接使用仓库提供的脚本：

```powershell
powershell -NoProfile -File qoder/scripts/start_powercontext.ps1 -Token "your-token" -OpenDashboard
```

### 1.4 就绪检查

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8765/health/ready
```

实测返回：

```json
{
  "status": "ready",
  "checks": {
    "runtime": "ready",
    "database": "ready",
    "access_mode": "enforced",
    "authentication_provider": "ready",
    "access_provider": "ready"
  }
}
```

---

## 2. 认证配置

| 位置 | 变量 | 说明 |
| --- | --- | --- |
| 服务端 | `POWERCONTEXT_SERVER_AUTH_TOKEN` | 启用 `ACCESS_MODE=enforced` 时必填 |
| 客户端 | `STUDYFLOW_POWERCONTEXT_TOKEN` | StudyFlow 后端环境变量，**只存在于后端** |

客户端以 `Authorization: Bearer <token>` 发送：

```python
headers = {"Authorization": f"Bearer {token}"}
```

实测行为：

| 请求 | 结果 |
| --- | --- |
| 不带 `Authorization` 访问 `/v1/server-info` | **HTTP 401** |
| 带正确令牌访问 `/v1/server-info` | **HTTP 200** |
| 带正确令牌访问 `/health/ready` | **HTTP 200**（`access_mode=enforced`） |

> 服务端 `ACCESS_MODE=disabled` 时 PowerContext 会忽略 `Authorization` 头，
> 此时客户端仍需填一个非空占位符（空值会形成非法 HTTP 头）。
> 本项目默认使用 `enforced`，让认证与作用域访问真正生效。

**安全边界**：`STUDYFLOW_POWERCONTEXT_TOKEN` 只写入后端进程环境，
浏览器永远看不到它；`/api/system/status` 只报告"凭据是否已配置"，不返回取值。

---

## 3. 用户记忆标识方案

### 3.1 一个学习者 = 一个 Scope

| 概念 | PowerContext 中的对应物 |
| --- | --- |
| 学习者 `user_id` | Scope 的外部引用 `external_references[{kind: "studyflow.learner_id", value: <user_id>}]` |
| 学习者的记忆集合 | 该 Scope 下的 Atomic Memory 条目 |
| 外部身份 → Scope 的映射 | scope binding `{integration: "studyflow", kind: "learner", external_id: <user_id>}` |

### 3.2 为什么这样设计

* Scope 是 PowerContext 原生的隔离边界，`scope.read` / `scope.contribute`
  访问检查以它为准；
* 外部引用与 scope binding 让**服务端**记住"哪个学习者对应哪个 Scope"，
  后端不需要维护任何本地数据库，重启后依然能解析；
* 学习者 ID 是应用层稳定标识，评审换一个 ID 就能立刻验证隔离。

### 3.3 实际调用的接口

| 用途 | 接口 |
| --- | --- |
| 通过绑定解析 Scope | `POST /v1/scope-bindings/resolve` |
| 兜底：按外部引用查 Scope | `GET /v1/scopes?external_reference_kind=studyflow.learner_id&query=<user_id>&query_field=external_reference_value` |
| 首次使用时创建 Scope | `POST /v1/scopes`（带 `idempotency_key`） |
| 持久化绑定 | `PUT /v1/scope-bindings` |

> 注意：`GET /v1/scopes` 的 `query` 与 `query_field` **必须同时提供**，
> 只给 `query` 会返回 422 `query and query_field must be provided together`。

实测（学习者 `student_demo_001`）：

```json
POST /v1/scope-bindings/resolve
→ 200 {"scope_id": "scp_64abdjagbdpaawfny60wyz9hr", "title": "StudyFlow · student_demo_001", ...}
```

---

## 4. 写入方式

### 4.1 新建记忆条目

```http
POST /v1/memory/remember
Content-Type: application/json
Authorization: Bearer <token>

{
  "scope_id": "scp_64abdjagbdpaawfny60wyz9hr",
  "kind": "learning_record",
  "text": "key: learning_record:2026-10-25T09:00:00+00:00_rec_ecb5c04b2b0b\ncourse: 数据库系统\ntopic: BCNF 分解\n...\n---\n在「BCNF 分解」上完成了一次学习活动。本次练习结果错误。自评掌握程度 2/5。",
  "reason": "learning-record"
}
```

响应（`MemoryMutationResponse`）：

```json
{
  "changed": true,
  "records": [
    {
      "artifact": {"family": "atomic-memory", "artifact_id": "art_f6baa45558da470faebe897b27f5de33", "revision": 1},
      "kind": "learning_record",
      "text": "...",
      "state": "active",
      "state_version": 0,
      "merged_into_id": null
    }
  ]
}
```

### 4.2 记忆条目类型

| `kind` | `key` | 作用 |
| --- | --- | --- |
| `learner_profile` | `learner_profile` | 学习档案（课程、目标、偏好、重点关注） |
| `learning_record` | `learning_record:<时间戳>_<id>` | **权威**的学习事件日志，只追加 |
| `knowledge_state` | `knowledge_state:<知识点>` | 掌握度投影，派生自事件日志 |
| `weakness` | `weakness:<知识点>` | 薄弱点及其判定原因 |
| `review_plan` | `review_plan:<日期>` | 当日复习计划快照 |
| `preference` | `preference:<名称>` | 讲解偏好 |

---

## 5. 更新方式（修订而非重复追加）

### 5.1 为什么要修订

如果每次练习都追加一条"BCNF 分解很薄弱"，记忆里会堆满互相矛盾的陈述，
检索时无法判断哪条是最新的。StudyFlow 的做法是**同一个 key 原地修订**，
让 PowerContext 的修订机制保存版本历史。

### 5.2 实际调用

```http
# 1) 读取当前条目与它的 ETag
GET /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}
→ 200  ETag: "revision:1"

# 2) 用 If-Match 条件修订
PUT /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}
If-Match: "revision:1"
Content-Type: application/json

{"content": {"kind": "knowledge_state", "text": "key: knowledge_state:BCNF 分解\n...mastery: 0.4300...\n---\n..."}}
→ 200  {"revision": 2, "artifacts": [{"family": "atomic-memory", "artifact_id": "...", "revision": 1}], ...}
```

实测结果：

| 步骤 | 结果 |
| --- | --- |
| 第 1 次写入 | `action=created`，revision 1 |
| 第 2 次写入同一 key | `action=revised`，revision 2，**artifact_id 不变** |
| 修订后重新列表 | 该 key 的条目**仍然只有 1 条**，内容已更新 |
| 历史版本 | PowerContext 在响应 `artifacts` 中保留 revision 1 的引用 |

条件修订还天然提供了并发保护：`If-Match` 不匹配时 Server 会拒绝，
避免两个写入方互相覆盖。

---

## 6. 检索方式

### 6.1 列出全部记忆

```http
POST /v1/memory/entries/list
{"scope_id": "...", "limit": 100}
→ 200 {"entries": [ ... ], "next_cursor": null}
```

### 6.2 按关键词检索

```http
POST /v1/memory/search
{"scope_id": "...", "query": "候选码", "limit": 5}
→ 200 {"mode": "text", "hits": [{"memory": {...}, "score": 0.0163, "matched_by": ["text"]}]}
```

> 未配置 Embedding 模型时 Server 使用全文检索（`mode=text`）。
> 本项目显式写入记忆、显式读取记忆，不依赖向量检索。

### 6.3 为什么不用 `POST /v1/context/prepare`

`/v1/context/prepare` 面向的是"从已捕获的 Source 中抽取并准备上下文"，
需要配置生成/嵌入流水线。在未配置模型的 Server 上实测返回：

```json
{"schema": "powercontext.prepared-context.v1", "status": "empty", "content": null, "content_bytes": 0}
```

StudyFlow 的记忆是**显式写入**的 Atomic Memory 条目，因此以
`/v1/memory/entries/list` 与 `/v1/memory/search` 作为召回通道，
再在应用层组装上下文——这条路径不需要任何模型，也更容易向评审解释。

---

## 7. 记忆持久化验证

### 7.1 方法

| 验证 | 做法 | 结果 |
| --- | --- | --- |
| 跨请求 | 写入后用另一个 HTTP 请求读回 | 读回内容一致 |
| 跨进程 | 在**全新 Python 进程**中用全新连接读取 | `read_memory_once.py` 能读到同样的 `scope_id` 与记录 |
| 跨连接 | 新建 `PowerContextClient` 实例读取 | 读回成功 |
| 跨重启 | 重启 PowerContext Server 后再读 | 数据仍在（SQLite 文件持久化） |
| 重启后版本 | 修订产生的 revision 2 依然有效 | `GET` 返回 `"revision:2"` |

### 7.2 全新进程读回的实测输出

```bash
python qoder/scripts/read_memory_once.py student_demo_001
```

```json
{
  "user_id": "student_demo_001",
  "scope_id": "scp_64abdjagbdpaawfny60wyz9hr",
  "record_count": 6,
  "entry_count": 12,
  "records": [{"topic": "函数依赖", "notes": "主属性判断已经掌握", "artifact_id": "art_765300739f6b44c788fe5e149b7bcfe7", "revision": 1, ...}]
}
```

### 7.3 服务端操作证据

`qoder/evidence/powercontext-server-operations.json` 记录了 PowerContext Server
自己的结构化日志中与 StudyFlow 相关的操作，含 `request_id`、`status_code`、
`duration_ms` 与 `operation`。一次完整演示的统计：

```text
create_scope            1
get_artifact           20
get_server_info         2
list_memory_entries    72
list_scopes             1
remember_memory        12
replace_artifact       20
resolve_scope_binding  40
set_scope_binding      40
```

`remember_memory` 与 `replace_artifact` 同时存在，正说明写入既包含新建，
也包含对同一 artifact 的修订。

重新生成：

```bash
python qoder/scripts/export_server_log.py
```

---

## 8. 实际调用示例（可直接复现）

```bash
# 前置：PowerContext 已启动，令牌为 <token>

SCOPE=$(curl -s -X POST http://127.0.0.1:8765/v1/scope-bindings/resolve \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"allow_default":false,"binding_keys":[{"integration":"studyflow","kind":"learner","external_id":"demo"}]}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['scope_id'])")

# 写入
curl -s -X POST http://127.0.0.1:8765/v1/memory/remember \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d "{\"scope_id\":\"$SCOPE\",\"kind\":\"knowledge_state\",\"text\":\"key: knowledge_state:BCNF 分解\nmastery: 0.4300\n---\n示例记忆。\"}"

# 读取
curl -s -X POST http://127.0.0.1:8765/v1/memory/entries/list \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d "{\"scope_id\":\"$SCOPE\",\"limit\":10}"

# 检索
curl -s -X POST http://127.0.0.1:8765/v1/memory/search \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d "{\"scope_id\":\"$SCOPE\",\"query\":\"BCNF\",\"limit\":5}"
```

---

## 9. 常见错误与解决方式

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `401 Unauthorized` | 未带令牌，或令牌与服务端不一致 | 客户端发送 `Authorization: Bearer <token>`；确认两端令牌相同 |
| `403 Forbidden` | 令牌有效但无权访问该 Scope | 确认用当前学习者解析出的 `scope_id`；检查 Access 绑定 |
| `422 invalid_request`（`query and query_field must be provided together`） | `GET /v1/scopes` 少传 `query_field` | 补上 `query_field=external_reference_value` |
| `404 scope_not_found` | 绑定或 Scope 不存在 | 先 `POST /v1/scopes` 创建，或检查 `external_id` |
| `409 Conflict` | `If-Match` 修订号过期 | 重新 `GET` 条目取最新 ETag 后再修订 |
| `AUTH_TOKEN requires ACCESS_MODE=enforced or legacy AUTH_ENABLED=true` | 设了令牌但访问模式是 `disabled` | 设置 `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` |
| `recovery_required` 拒绝启动 | 删库时漏删 WAL / SHM / 迁移状态 | 一并删除 `*.db-wal`、`*.db-shm`、`*.db.pc-migration-state`、`.cursor-key`（`reset_demo.ps1` 已封装） |
| 客户端报"拒绝明文 HTTP" | PowerContext 客户端拒绝把记忆内容通过明文 HTTP 发往非回环地址 | 使用 `127.0.0.1` / `localhost`，或部署到 HTTPS 之后 |
| Server 启动后 `/v1/access/me` 返回 503 | 访问控制提供方未就绪 | 该接口在 `enforced` 模式下依赖内部组件，不影响记忆读写；StudyFlow 不用它 |

---

## 10. 与官方文档的关系

* 接口契约以仓库内 `openapi/powercontext.yaml` 与
  `src/powercontext/http/_generated/operations.py` 为准；
* 本文中的请求体与响应体均为**真实运行**得到的样本，已裁剪到便于阅读的长度；
* StudyFlow 只使用 PowerContext 的公开 HTTP 接口，**未修改**其任何源码；
* 未使用 seekDB，也未引入 seekDB 相关依赖。
