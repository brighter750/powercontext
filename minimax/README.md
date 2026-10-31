# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

> MiniMax × OceanBase 赛道作品
> 题目：规有所依——面向软件持续演进的 SDD 上下文管理系统
> 仓库分支：`2026-competition-ecosystem`，全部资产位于本目录 `minimax/`

---

## 1. 一页技术说明

### 1.1 要解决的问题

AI 开发助手改代码时，需要同时知道三件事：**当前应遵守哪些规格**、**应该改哪些代码**、
**怎么验证**。资料分散、规格变更或任务中断，会导致遗漏约束、使用过时信息或重复劳动。

本作品实现一个**工程上下文模块** `sddctx`：按开发任务定位适用的规格、相关代码与测试，
组装成可直接交给开发助手的上下文；同时借助 PowerContext 保存任务进展，
使上下文在模块重启后仍可复用。

### 1.2 模块结构

```text
minimax/
├── sddctx/                          # ← 上下文管理模块（本次交付的核心源码）
│   ├── cli.py                       # 命令行入口：init / import / search / context / state / patch / doctor
│   ├── importer.py                  # 导入规格（Markdown → 条目）、代码（AST → 符号）、测试（AST → 用例）
│   ├── tokenizer.py                 # 中英文混合分词（标识符切词 + 中文 bigram），写入与查询同口径
│   ├── retrieval.py                 # 检索 + 上下文组装 + Markdown 渲染
│   ├── config.py                    # 配置（全部来自环境变量，不硬编码凭据）
│   ├── models.py                    # 领域模型：SpecItem / CodeSymbol / TestCase / TaskState / DevContext
│   ├── store/                       # 资料库（数据库）
│   │   ├── base.py                  #   统一协议与统计结构
│   │   ├── mysql_like.py            #   MySQL 协议实现：建表 / 写入 / 倒排索引 / 检索
│   │   ├── seekdb_store.py          #   嵌入式 seekdb 后端（默认）
│   │   └── oceanbase_store.py       #   OceanBase 服务端后端（DSN）
│   ├── state/                       # 任务状态（复用 PowerContext）
│   │   └── powercontext_state.py    #   Work Contract + Source + Atomic Memory 写入，检索读取
│   └── llm/                         # MiniMax 接入
│       ├── minimax_client.py        #   Anthropic / OpenAI 兼容协议客户端
│       └── coder.py                 #   提示词组装、diff 解析、补丁应用（git apply + 容错回退）
├── business_project/                # ← 验证样例业务工程（真实可运行、可测试）
│   ├── specs/                       #   规格文档（13 条规格条目）
│   ├── src/order_pricing/           #   业务实现
│   └── tests/                       #   目标功能测试 + 回归测试
├── demo/run_demo.py                 # ← 验证样例编排脚本（端到端闭环）
├── scripts/capture.py               # ← 证据采集（命令 / 退出码 / 代码版本）
├── evidence/                        # ← 技术使用记录（数据库查询、MiniMax 调用、状态写入与读取）
└── patches/                         # ← 修改前后代码快照与差异
```

### 1.3 上下文选择方式

上下文不是拍脑袋拼出来的，而是**由数据库检索结果驱动**：

1. **导入**：规格按 `## <规格编号> <标题>` 切成条目；代码用 `ast` 抽函数/类/常量及其行号；
   测试用 `ast` 抽用例，并回溯用例里引用的规格编号。
2. **建索引**：每条资料按中英文混合分词写入倒排索引表 `sdd_token(token, item_kind, item_id, tf)`。
3. **检索**：把任务描述分词后对 `sdd_token` 做一次真实 SQL 查询
   （`WHERE token IN (...) GROUP BY item_kind, item_id ORDER BY SUM(tf) DESC`）。
4. **回表**：按命中 ID 读回完整规格正文、代码符号与测试用例。
5. **补链**：
   - 规格正文里点名的标识符 → 直接定位到**需要修改的代码符号**（规格 → 代码）；
   - 规格编号 → 定位到**覆盖该规格的测试**（规格 → 测试）。
6. **组装**：输出 `适用规格 / 需要修改的代码 / 功能与兼容性测试 / 已完成 / 待办 / 已有验证结果`
   六段式上下文，并附上数据库命中明细，便于核对来源真实存在。

规格更新后重新导入时，条目 `content_hash` 变化 → `revision` 递增 → 倒排索引重建，
检索立刻反映新内容（见 `evidence/db/spec-update-verification.md`）。

### 1.4 三项指定技术的分工

| 技术 | 承担的角色 | 在本作品中的落点 |
| --- | --- | --- |
| **MiniMax** | 参与本次任务的代码生成/修改 | `sddctx/llm/minimax_client.py` 调用 MiniMax 文本模型；`sddctx/llm/coder.py` 把开发上下文组装成提示词、解析 unified diff 并落盘。本次实际产出补丁的模型为 **MiniMax-M3.1-Flash**（Anthropic 兼容端点 `https://api.minimaxi.com/anthropic`）。提示词、原始响应与调用记录见 `evidence/minimax/`；取用方式的如实说明见 `evidence/README.md` §1。 |
| **seekdb / OceanBase** | 存储工程资料或索引，检索结果用于构造任务上下文 | `sddctx/store/`。默认使用**嵌入式 seekdb**（`pylibseekdb 1.4.0.post2`，引擎版本 `5.7.25-OceanBase seekdb-v1.4.0.0`），落库规格 13 条、代码符号 21 个、测试 29 条、倒排索引 1783 条；也可通过 `SDDCTX_DB_BACKEND=oceanbase` 切换到 OceanBase 服务端。检索命中明细见 `evidence/db/`。 |
| **PowerContext** | 保存任务进展或验证结果，并在后续运行中读取、使用 | `sddctx/state/powercontext_state.py`。写入走 PowerContext 真实接口：Work Contract（`/v1/work/contracts/create`）、Source 证据（`/v1/sources/content`）、Atomic Memory（`/v1/memory/remember`）；读取走 `GET /v1/scopes/{scope_id}/sources` 与 `POST /v1/memory/search`。 |

三者串成一条真实链路：

```text
任务描述
   │  分词 + 倒排索引检索
   ▼
seekdb / OceanBase  ──►  适用规格 · 相关代码 · 相关测试
   │                            │
   │                            ▼
   │                    组装开发上下文（Markdown）
   │                            │
   │                            ▼
   │                       MiniMax 生成 unified diff
   │                            │
   │                            ▼
   │                     应用补丁 → 运行功能与回归测试
   │                            │
   ▼                            ▼
PowerContext  ◄──────── 保存任务状态（契约 + Source + Memory）
   │
   └─► 模块重启后读取状态 → 继续参与后续上下文构造
```

### 1.5 一次真实开发任务

* **任务**：实现阶梯满减（`SPEC-PRICING-102`）——按会员折后小计命中
  `1000 / 5000 / 10000` 三档门槛，只取最高一档，不叠加；会员折扣先于阶梯满减。
* **必须保持**：`SPEC-PRICING-101`（会员折扣）、`SPEC-SHIPPING-201/202`、`SPEC-INVOICE-301/302`、
  `SPEC-COMPAT-401…404`（既有函数签名与边界行为）。
* **修改文件**：`src/order_pricing/discounts.py` 的 `tiered_discount_rate`。
* **目标测试**：`tests/test_tiered_discount.py`（本次新增）。
* **回归测试**：`test_money.py` / `test_member_discount.py` / `test_shipping.py` / `test_invoice.py`。
* **结果**：修改前 **9 failed / 20 passed**（退出码 1，全部为目标行为缺失导致的 `AssertionError`）；
  修改后 **29 passed**（退出码 0）。两份日志中的 6 个测试文件 SHA-256 完全一致。

---

## 2. 目录导航

| 我想看 | 去哪里 |
| --- | --- |
| 怎么跑起来 | [`RUNBOOK.md`](RUNBOOK.md) |
| 验证样例（任务、规格、初始数据、步骤、预期结果、重置） | [`demo/README.md`](demo/README.md) |
| 验证样例业务工程说明 | [`business_project/README.md`](business_project/README.md) |
| 修改前后的代码与差异 | [`patches/`](patches/) |
| 技术使用记录总览（含 MiniMax 取用方式的如实说明） | [`evidence/README.md`](evidence/README.md) |
| 测试运行日志（命令 / 退出码 / 代码版本 / 测试文件指纹） | [`evidence/logs/`](evidence/logs/) |
| 数据库查询记录 | [`evidence/db/`](evidence/db/) |
| MiniMax 开发记录 | [`evidence/minimax/`](evidence/minimax/) |
| PowerContext 状态写入与读取记录 | [`evidence/state/`](evidence/state/) |
| 六项必做条件的逐项结论（由脚本自动核对生成） | [`ACCEPTANCE.md`](ACCEPTANCE.md) |

---

## 3. 依赖与版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.12（支持 3.11+） | |
| PowerContext | 1.4.1（`powercontext[cli,server]`） | 任务状态保存与读取 |
| pylibseekdb | 1.4.0.post2 | 嵌入式 seekdb（数据库后端） |
| seekdb 引擎 | `5.7.25-OceanBase seekdb-v1.4.0.0` | 实际运行版本，见 `evidence/db/` |
| aiomysql | 0.3.2 | MySQL 协议客户端 |
| httpx | ≥0.28,<1 | 调用 PowerContext HTTP 接口 |
| pytest | ≥8,<9 | 业务工程测试 |
| MiniMax | 见 `evidence/minimax/` | 经 Anthropic / OpenAI 兼容端点调用 |

---

## 4. 已知限制

1. 检索使用**词元倒排索引 + 词频排序**，不是向量语义检索；规格正文点名的标识符会被
   额外用于“规格 → 代码”定位，以此弥补中英文表述不一致带来的漏检。
2. 规格解析只识别 Markdown 二级标题 `## <规格编号> <标题>`，其他层级标题会被并入上一条目正文。
3. 代码与测试的符号抽取仅支持 Python（`ast`）。
4. PowerContext 状态读取优先走 `memory/search`，命中不到时回退到 Source 证据读取。
5. MiniMax 补丁应用优先用 `git apply`；失败时按上下文块做容错替换，仍失败则保留模型原始响应
   并报错，不会静默写入半成品。
6. 嵌入式 seekdb 在 Windows / Linux / macOS(x64) 有官方 wheel；若目标平台缺失，
   可改用 `SDDCTX_DB_BACKEND=oceanbase` 连接 OceanBase 服务端（同一套实现与表结构）。
