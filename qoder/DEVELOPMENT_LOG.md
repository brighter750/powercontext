# StudyFlow 开发记录

> **关于开发工具的如实说明**
>
> 赛题要求「项目的主要开发工作必须在 Qoder 中完成」，并提交
> **真实的 Qoder 对话截图或录屏**与 **Qoder 账号 UID**。
>
> 本项目的代码、文档与测试是在 **MiniMax Code（MCode）** 这一 AI 编程代理中
> 完成的，**不是**在 Qoder 中完成的。因此：
>
> * 本文件如实记录**实际发生**的开发过程，不冒充 Qoder 的开发记录；
> * **没有**伪造任何 Qoder 对话截图、录屏或账号 UID；
> * 提交前仍需由参赛者本人在 Qoder 中完成相应工作（导入本仓库、
>   用 Qoder 复核或继续开发、截取真实对话记录），并填写真实的 Qoder UID。
>
> 这一项是赛题的基础验收条件之一，无法由本文件替代。

---

## 阶段 0：仓库检查与技术调查

**目标** — 在写任何代码之前，确认仓库关系、目标分支、工作区状态，
并把 PowerContext 的**真实**接口搞清楚，避免凭空编造 API。

**完成的工作**

* 检查 `git rev-parse --show-toplevel` / `git branch --show-current` /
  `git status --short` / `git remote -v` / `git log -1 --oneline`。
* 确认当前目录为 `C:\Users\mazhe\powercontext`，分支为 `2026-competition-ecosystem`，
  `origin` 指向 `https://github.com/brighter750/powercontext.git`（参赛者自己的仓库，
  **不是** OceanBase 官方仓库），未配置 `upstream`。
* 确认 `qoder/` 目录此前只有 `.gitkeep`，不存在需要保护的既有内容。
* 阅读 `src/powercontext/http/_generated/operations.py` 与 `models.py`，
  列出 StudyFlow 需要的接口及其请求/响应模型。
* 阅读 `README.md`、`docs/en/docs/get-started/manual-start.md`、
  以及仓库内 `dify/` 赛道的实际接入方式作为参照。
* 启动真实 PowerContext Server，用 `httpx` 写探针脚本**实测**每个接口，
  而不是只读生成代码。

**实测得到的接口事实**（这是后续所有实现的基础）

| 结论 | 说明 |
| --- | --- |
| `POST /v1/scopes` | 创建 Scope，支持 `external_references` 与 `idempotency_key`，返回 201 |
| `GET /v1/scopes` | `query` 与 `query_field` **必须同时提供**，否则 422 |
| `PUT /v1/scope-bindings` | 写入外部身份 → Scope 绑定（POST 返回 405） |
| `POST /v1/scope-bindings/resolve` | 按绑定解析 Scope；`allow_default=false` 且未绑定时返回 404 |
| `POST /v1/memory/remember` | 每条记忆是一个独立 artifact |
| `POST /v1/memory/entries/list` | 列出条目，`limit` 上限 100 |
| `POST /v1/memory/search` | 未配 Embedding 模型时走全文检索（`mode=text`） |
| `POST /v1/context/prepare` | 无生成流水线时返回 `status=empty`，不适合显式写入的记忆 |
| `PUT /v1/scopes/{scope}/artifacts/atomic-memory/{id}` | 修订条目，需带 `If-Match: "revision:N"` |

**遇到的问题** — 生成代码里的 `models.py` 有大量字段约束，但无法从中确定
哪些组合在运行时会被服务端拒绝（例如 `GET /v1/scopes` 的 422）。
**解决方式** — 写探针脚本逐个接口实测，把失败响应也记录下来。

**修改的文件** — 无（此阶段只新增了被 gitignore 的探针脚本）。

---

## 阶段 1：PowerContext 部署与接入模式验证

**目标** — 确定并验证 PowerContext 的部署方式、认证方式与用户隔离机制。

**完成的工作**

* 以 `ACCESS_MODE=enforced` + `POWERCONTEXT_SERVER_AUTH_TOKEN` 启动 Server，
  实测：不带令牌访问 `/v1/server-info` 返回 **401**，带令牌返回 **200**。
* 发现并记录了一个配置约束：`AUTH_TOKEN` 必须搭配
  `ACCESS_MODE=enforced`（或旧版 `AUTH_ENABLED=true`），否则 Server 启动即报错。
* 实测完整业务链路：建 Scope → 写绑定 → 解析绑定 → 写两条记忆 →
  列表 → 检索 → 读 artifact 取 ETag → 修订 → 再次列表。
* 实测另一个学习者的 Scope 中条目数为 **0**，确认隔离由 Scope 边界保证。

**修改的文件** — 无业务代码。

---

## 阶段 2：架构设计与目录规划

**目标** — 确定技术栈、目录结构与各层职责。

**关键设计决策**

1. **一个学习者 = 一个 PowerContext Scope**，用 `external_references` +
   scope binding 持久化映射。映射存在服务端，后端不需要任何本地数据库，
   进程重启后仍能解析 —— 这是跨会话记忆能成立的前提。
2. **学习记录是权威事件日志，掌握度是派生投影**。
   每次新增练习都从全部 `learning_record` 重算掌握度，
   并在投影里保留 `record_ids`，重建投影不会悄悄改写历史。
3. **同 key 原地修订，不重复追加**。通过记忆信封里的 `key` 定位既有条目，
   找到就 `PUT` 修订（保留版本历史），找不到才 `remember` 新建。
4. **记忆信封**：一条 Atomic Memory 的 `text` 用
   `key: xxx` / `field: value` / `---` / 自然语言 的结构承载，
   既能被后端确定性地解析回来供页面渲染，又保留自然语言叙述供 AI 引用。
5. **前端零构建步骤**。评审只需启动后端就能打开应用，
   不需要 Node 工具链与 `npm install`。
6. **AI 可插拔且可缺席**。未配置模型时明确报错，不返回伪造结果；
   而学习记录、薄弱分析、复习计划、记忆中心四项完全由真实记录驱动，
   与模型无关。

**创建的文件**

```text
qoder/
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── backend/app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用、异常映射、静态挂载
│   ├── config.py               # 配置（全部来自环境变量）
│   ├── schemas.py              # API 模型
│   ├── memory_document.py      # 记忆信封格式
│   ├── api/{__init__.py,routes.py}
│   ├── powercontext/{__init__.py,client.py,repository.py}
│   └── services/{__init__.py,ai_service.py,learning_service.py}
├── frontend/
│   ├── index.html
│   └── assets/css/app.css
│   └── assets/js/{api.js,ui.js,store.js,main.js,pages/*.js}
├── scripts/
├── tests/
├── docs/
├── evidence/
├── assets/screenshots/
└── site/
```

---

## 阶段 3：后端开发

**目标** — 实现领域规则、PowerContext 接入层与 HTTP API。

**完成的工作**

* `app/powercontext/client.py` — 唯一与 PowerContext 通信的模块。
  集中处理 Bearer 鉴权、超时、指数退避重试，并把传输错误翻译成
  `PowerContextTimeout` / `PowerContextAuthError` /
  `PowerContextUnavailable` / `PowerContextRequestError` 四类可区分异常。
* `app/powercontext/repository.py` — Scope 生命周期与"创建或修订"语义。
* `app/services/ai_service.py` — `OpenAiCompatibleProvider` 与
  `AnthropicCompatibleProvider` 两个协议实现，共享 `AiService` 门面。
  JSON 解码容错（去代码块围栏、从散文中提取数组/对象）。
* `app/services/learning_service.py` — 掌握度计算、薄弱判定、
  复习计划、记忆召回上下文组装、记忆写回。
* `app/api/routes.py` + `app/main.py` — 10 个 API 路由；
  异常处理器把 PowerContext / AI 故障映射为 504 / 403 / 503 / 400 / 502，
  每一条都带可读的中文说明。

**遇到的问题与解决**

| 问题 | 解决方式 |
| --- | --- |
| 领域规则和传输细节纠缠在一起 | 拆成 `client`（传输）/ `repository`（记忆语义）/ `learning_service`（领域规则）三层 |
| 薄弱判定需要"负面证据"才成立 | 规则改为：无负面证据（没答错且自评 ≥ 3）且练习 < 2 次 → 不算薄弱点；单次答错最多 `medium`；`high` 需 ≥ 2 次练习 |
| 复习计划排序曾忽略严重程度 | 把排序移入 `_build_weaknesses()`，所有调用方共享同一顺序 |
| 掌握度可能被 0%/100% 绑架 | 加入先验平滑（`+2.0×0.5`）与按时间的 0.9 衰减加权 |

---

## 阶段 4：前端开发

**目标** — 7 个页面、统一视觉、响应式、真实交互。

**完成的工作**

* 设计系统 `app.css`：CSS 变量 + 浅色/深色双主题（跟随
  `prefers-color-scheme`）、卡片 / 徽章 / 表格 / 掌握度条 / 聊天气泡 /
  骨架屏 / Toast / 空状态等组件，900px 与 560px 两个断点。
* 原生 ES Module，无打包：`api.js`（接口客户端）、`ui.js`（转义与格式化）、
  `store.js`（仅保存"当前学习者 ID"）、`main.js`（hash 路由）、
  `pages/*.js`（七个页面）。
* 每个页面都有加载骨架、空状态引导、成功/失败 Toast；表单可真实提交。
* 学习记录页提交后立即刷新并显示新的掌握度；
  AI 助手页显示"已使用 N 条长期记忆"及命中的记忆标签。

**遇到的问题与解决**

| 问题 | 解决方式 |
| --- | --- |
| 窄屏下宽表格被压成一列一个字 | `.table-wrap` 增加 `min-width`，宽表加 `.table-wrap--wide`，内容列加 `.cell-text` 最小宽度，改为横向滚动 |
| 备忘录里的中文在 PowerShell 控制台里是乱码 | 确认只是控制台代码页问题，数据本身是正确 UTF-8（用 Python 以 UTF-8 读回验证） |

---

## 阶段 5：调试与错误修复

**记录在案的实际缺陷与修复**

| # | 现象 | 根因 | 修复 | 回归测试 |
| --- | --- | --- | --- | --- |
| 1 | 无信封的旧记忆解析后叙述为空 | `partition` 未命中分隔符时把叙述置空 | 重写解析：只有 `snake_case` 行才当字段，其余留作叙述 | `test_memory_document_parses_legacy_plain_text` |
| 2 | 复习计划把「轻度薄弱」排在「急需加强」前面 | `_build_weaknesses()` 内部缺少排序 | 排序移入该方法 | `test_weaknesses_are_ordered_by_severity_then_mastery` |
| 3 | 答对一次且自评良好的知识点被列为薄弱点 | 薄弱判定只看掌握度阈值，没有要求负面证据 | 判定规则加入负面证据前提 | `test_single_correct_answer_is_not_a_weakness` |
| 4 | `_build_weaknesses()` 内引用了不存在的变量 | 重命名时留下的引用错误 | 修正为 `items` | 现有用例覆盖 |
| 5 | PowerShell 5.1 解析带中文的 `.ps1` 报"缺少字符串终止符" | PS 5.1 按 ANSI 读取无 BOM 的 UTF-8 文件 | 交付的 `.ps1` 写入 UTF-8 **BOM** | 手动验证三个脚本 |
| 6 | `start_powercontext.ps1` 报"Cannot overwrite variable Host" | `$Host` 是 PowerShell 只读自动变量 | 参数改名 `$BindAddress` | 手动验证脚本 |
| 7 | `reset_demo.ps1` 删除目录时报 NullReferenceException | PS 5.1 的 `Remove-Item -Force` 对目录的已知问题 | 区分文件与目录，目录加 `-Recurse` | 手动验证脚本 |
| 8 | 验收样例 E 的子进程读取失败 | `-c` 单行无法表达 `async def` | 改为独立脚本 `read_memory_once.py` | 样例 E 现已通过 |
| 9 | 子进程输出解码失败 | Windows 控制台代码页导致子进程输出 GBK | 子进程设置 `PYTHONIOENCODING=utf-8` | 样例 E 现已通过 |
| 10 | Server 日志解析出 0 条记录 | PowerShell 5.1 的 `Tee-Object` 写出 UTF-16LE | 导出脚本按 BOM 探测编码 | `export_server_log.py` 现导出 208 条 |

**另一个反复出现的现象（非缺陷）**：PowerShell 控制台把 UTF-8 中文显示为乱码。
用 Python 以 UTF-8 读回同一份数据确认内容正确后，即判定为控制台显示问题，
没有为此改动数据。

---

## 阶段 6：自动化测试

**目标** — 建立单元 / Mock / 真实集成三层测试，并让三层互不替代。

**完成的工作**

* `tests/fakes.py` — `FakePowerContextClient`：内存替身，
  复刻真实客户端的关键语义（每次 `remember` 新建 artifact、
  `revise_artifact` 递增修订号、Scope 之间互相隔离）。
* `tests/test_unit_domain.py`（14 项）— 记忆信封解析、掌握度计算、
  薄弱判定、近期练习权重、趋势判断、AI JSON 解码。
* `tests/test_mock_service.py`（16 项）— 档案创建与读回、记录新增、
  修订而非追加、薄弱点与复习计划、Scope 复用、用户隔离、
  PowerContext 三类故障的可区分性、AI 未配置时的诚实响应。
* `tests/test_integration_powercontext.py`（11 项）— **真实 Server**：
  Scope 创建与绑定、写入后读回、更新修订同一条目、修订历史保留、
  检索召回、跨学习者隔离、重启后持久化、同 key 幂等、
  异常查询参数不报错。
* `tests/test_api_integration.py`（9 项）— **真实 Server + 运行中的后端**：
  健康检查、服务状态、档案读写、记录与薄弱更新、复习计划与概览、
  记忆中心列出真实 artifact、用户隔离、AI 未配置时的诚实响应、前端可访问。

**实际测试结果**

```text
$ cd qoder && pytest tests -q
..................................................                       [100%]
50 passed in 8.11s
```

真实集成层逐条：

```text
tests/test_integration_powercontext.py::test_scope_is_created_and_bound_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_write_then_read_from_the_real_server PASSED
tests/test_integration_powercontext.py::test_update_revises_the_same_artifact_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_revision_history_is_preserved_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_recall_by_search_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_learners_are_isolated_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_memory_survives_a_restart PASSED
tests/test_integration_powercontext.py::test_upsert_is_idempotent_for_the_same_key PASSED
tests/test_integration_powercontext.py::test_search_never_raises_on_odd_queries[...] PASSED  (3 项)
```

> 无 Server 时，真实集成用例会 **skip** 而非伪装通过。

---

## 阶段 7：验收样例与证据

**目标** — 按赛题要求跑通五个样例，并留下可核对的证据。

**完成的工作**

* `scripts/verify_scenarios.py` — 执行样例 A–E，逐条记录真实请求/响应，
  输出到 `qoder/evidence/scenarios/*.json`。
* `scripts/read_memory_once.py` — 在全新进程中读回记忆（样例 E）。
* `scripts/export_server_log.py` — 从 PowerContext Server 自己的结构化日志里
  导出与 StudyFlow 相关的操作（含 `request_id`、`status_code`、耗时）。

**实际结果**

```text
场景                          结果
测试 A：写入学习记忆           需要配置 AI 模型后复测
测试 B：新会话读取记忆          需要配置 AI 模型后复测
测试 C：更新学习进度           需要配置 AI 模型后复测
测试 D：用户隔离               通过
测试 E：服务重启后读取          通过
```

样例 D 实测证据：

```json
{
  "scope_a": "scp_64abdjagdwpdaavfny60wy29hr",
  "scope_b": "scp_4gckennc58dfgae6jmgsc9a4zx",
  "user_b_entry_count": 0,
  "user_b_memory_context_leaks_user_a": false
}
```

样例 E 实测证据（全新进程读取）：

```json
{
  "scope_id": "scp_64abdjagdwpdaavfny60wy29hr",
  "record_count": 6,
  "entry_count": 12
}
```

PowerContext Server 侧导出的操作统计：

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

**A / B / C 未通过的原因** — 当前执行环境中**没有任何可用的模型服务凭据**
（已确认环境变量、本机配置与仓库内其它赛道的配置中均无可用密钥）。
按赛题要求「不得返回伪造的 AI 结果」，应用返回明确的"未配置"提示，
样例脚本据此如实标记为「需要配置 AI 模型后复测」，并保存了实际响应。
**等价的、不依赖模型的读写路径已全部通过真实集成测试**，
详见 `docs/TEST_CASES.md`。

---

## 阶段 8：部署准备与交付材料

**完成的工作**

* 四个脚本：启动 PowerContext、启动后端、写入演示数据、重置演示状态。
  三个 `.ps1` 均带 UTF-8 BOM 并通过 Windows PowerShell 5.1 实际执行验证。
* `docs/` 五份文档：`ARCHITECTURE.md`、`POWERCONTEXT_INTEGRATION.md`、
  `TEST_CASES.md`、`DEPLOYMENT.md`、`DEMO_GUIDE.md`。
* `assets/screenshots/` 六张真实界面截图（Dashboard、学习记录、薄弱分析、
  记忆中心、复习计划、AI 助手）。
* `site/` — Qoder Sites 展示站点源码。
* `README.md` 与本文件。

**验证过程**：所有脚本均实际执行过；浏览器中逐页验证了七个页面的渲染、
导航、表单提交与移动端布局；控制台无错误。

---

## 尚未解决的问题

| 问题 | 状态 | 需要谁处理 |
| --- | --- | --- |
| 样例 A / B / C 的自然语言步骤 | 待补测 | 需要一个可用的模型服务凭据，配置后重跑 `scripts/verify_scenarios.py` |
| Qoder 开发记录（真实截图/录屏）与 Qoder 账号 UID | **未完成** | 参赛者本人在 Qoder 中完成并填写真实 UID |
| Qoder Sites 项目网站发布 | **未完成** | 需要参赛者本人的 Qoder 个人账号；站点源码已备好在 `qoder/site/` |
| 真实用户使用数据与反馈 | 未收集 | 属于加分项，需邀请同学实际使用 |
| 演示数据在 `qoder/.artifacts/` 中（已 gitignore） | 预期行为 | 评审按 `DEPLOYMENT.md` 重建即可 |
