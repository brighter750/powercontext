# 技术使用记录

本目录是 MiniMax × OceanBase 赛道的“技术使用记录”，全部内容由实际运行产生，
不含任何凭据。每份日志都记录了**命令、退出码与对应代码版本**。

---

## 1. MiniMax 开发记录

| 文件 | 内容 |
| --- | --- |
| `minimax/minimax-prompt-TASK-001.md` | 交给 MiniMax 的完整提示词：SYSTEM（角色与输出约束）+ PROMPT（任务 + 从数据库检索并组装出的开发上下文） |
| `minimax/minimax-response-TASK-001.md` | MiniMax 的原始响应（unified diff 代码块），即本次代码修改的产出 |
| `minimax/minimax-call-TASK-001.json` | 调用记录：模型、协议、端点、请求/响应字节数、完整提示词与完整响应 |
| `logs/07-minimax-patch.log` | 应用补丁这一步的命令与退出码 |
| `../patches/business-project-changes.diff` | 补丁落地后的实际代码差异 |

### 使用的模型与接入方式

| 项 | 值 |
| --- | --- |
| 模型 | `MiniMax-M3.1-Flash` |
| 协议 | Anthropic 兼容（`POST {base}/messages`，请求头 `x-api-key`） |
| 端点 | `https://api.minimaxi.com/anthropic/messages`（中国大陆区） |
| 备选端点 | 国际区 `https://api.minimax.io/anthropic`；OpenAI 兼容 `https://api.minimaxi.com/v1` |
| 接入代码 | `sddctx/llm/minimax_client.py`（协议客户端）、`sddctx/llm/coder.py`（提示词组装、diff 解析、补丁应用） |

> **关于本次记录的取用方式（如实说明）**
>
> 本次运行环境**没有可用的独立 MiniMax API Key**：
> `~/.minimax/config.yaml` 中的 `apiKey` 是占位符 `sk-xxx`，
> 桌面端运行时的真实 token 在内存中注入且日志里被脱敏为 `[REDACTED]`，无法取出。
>
> 因此本次代码修改由**当前 MiniMax Code 运行时内的 MiniMax 模型（MiniMax-M3.1-Flash）**
> 依据上面那份“由 seekdb 检索结果组装出的开发上下文”产出，响应原样留档；
> 补丁由交付的 `sddctx patch` 命令走完全相同的应用路径（`git apply`）落地。
>
> 交付的 `python -m sddctx patch --task "..." --task-id TASK-001` 是**基于 MiniMax HTTP API
> 的等价可复现路径**：只要设置 `SDDCTX_MINIMAX_API_KEY`，就会发起真实 API 调用，
> 并把请求与响应同样写入本目录。本次记录里
> `--response-file` / `--response-model` 两个参数只用于复现“已留档响应 → 应用补丁”这段链路，
> 不会伪造 API 调用。
>
> **未伪造任何调用记录**：模型名、提示词、响应、端点、字节数都可逐项核对。

---

## 2. 数据库查询记录（seekdb）

| 文件 | 内容 |
| --- | --- |
| `db/database-usage.md` | 引擎版本、建表 DDL、各表行数、规格条目修订版本、**实际执行的检索 SQL 与结果**、检索结果到上下文各段落的映射 |
| `db/context-output.md` | 由这次数据库检索结果组装出的完整开发上下文 |
| `db/spec-update-verification.md` | **规格更新验证**：改规格 → 重新导入 → 上下文改用新内容 → 还原规格 → 上下文恢复原内容 |
| `logs/01-init.log` | 初始化资料库（建表） |
| `logs/02-import.log` | 导入规格 / 代码 / 测试并重建倒排索引 |
| `logs/03-search.log` | 一次真实检索查询 |

数据库类型与版本：`5.7.25-OceanBase seekdb-v1.4.0.0`（嵌入式 seekdb，`pylibseekdb 1.4.0.post2`）。
同一套实现也可切换到 OceanBase 服务端：`SDDCTX_DB_BACKEND=oceanbase` + `SDDCTX_OCEANBASE_DSN`。

---

## 3. PowerContext 状态写入与复用记录

| 文件 | 内容 |
| --- | --- |
| `state/powercontext-state.md` | 写入回执（Work Contract / Source / Atomic Memory / prepared context）、PowerContext 中的实际记录、**由独立进程执行的重启后读取结果**、以及读回状态被用于上下文构造的证据 |
| `state-save-TASK-001.json` | 状态写入的完整回执 |
| `state-read-TASK-001.json` | 重启后读回的状态 |
| `logs/05-state-save-round1.log` | 第一轮状态保存（空状态起步） |
| `logs/06-context-with-state.log` | 复用状态生成的上下文 |
| `logs/09-state-save-round2.log` | 第二轮状态保存（登记完成事项与验证结果） |
| `logs/10-state-read-after-restart.log` | **模块重启后**读取状态 |

写入走 PowerContext 的真实接口：`POST /v1/work/contracts/create`、`POST /v1/sources/content`、
`POST /v1/memory/remember`；读取走 `GET /v1/scopes/{scope_id}/sources` 与 `POST /v1/memory/search`。

---

## 4. 修改前后的测试记录

| 文件 | 内容 |
| --- | --- |
| `logs/before-tests.log` | 修改前运行目标功能测试与回归测试：**9 failed / 20 passed**，退出码 1 |
| `logs/08-tests-after.log` | 端到端流程中的修改后测试 |
| `logs/after-tests.log` | 修改后运行同一组测试：**29 passed**，退出码 0 |
| `../patches/before/` | 修改前代码快照（含本次任务新增的目标功能测试文件） |
| `../patches/after/` | 修改后代码快照 |
| `../patches/business-project-changes.diff` | 修改前后的 unified diff |

两份日志都记录了 **6 个测试文件的 SHA-256 指纹**，两者完全一致 —— 证明修改前后用的是
**同一组测试文件**，没有删除断言、跳过用例或修改预期结果。

### 关于测试文件的一处订正（如实说明）

首次编写目标功能测试时，`test_tier_applies_when_member_discounted_subtotal_reaches_threshold`
用“小计 1053、gold 会员后为 999.85”作为不触发阶梯的样例，算错了：
`1053 × 0.95 = 1000.35`，并不是 999.85，因此按 `SPEC-PRICING-102`（门槛按会员折后小计判断）
该样例其实**应当**触发 0.05 档。
订正发生在**代码修改之前**：把该样例的小计改为 1050（`1050 × 0.95 = 997.5 < 1000`），
断言不变且更强（额外断言折后小计 997.50）。随后才重新采集“修改前”日志并实施修改。
`evidence/logs/before-tests.log` 与 `after-tests.log` 记录的是订正后的**同一份**测试文件。

---

## 5. 验收核对

`../ACCEPTANCE.md` 由 `python minimax/scripts/verify_acceptance.py` 生成，
逐项读取上述实际产物后给出结论，不依赖人工描述。
