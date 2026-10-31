# 运行说明（RUNBOOK）

> 全部命令在仓库根目录执行。示例使用 Bash 语法；Windows PowerShell 下把
> `export VAR=value` 换成 `$env:VAR='value'`，把 `source .env` 换成
> `Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#=]+)=(.*)$') { Set-Item -Path "Env:$($matches[1])" -Value $matches[2] } }`。

---

## 1. 环境与依赖版本

| 组件 | 版本 | 必需 | 说明 |
| --- | --- | --- | --- |
| Python | 3.11 ~ 3.14（本机验证 3.12.16） | 是 | |
| [uv](https://docs.astral.sh/uv/) 或 pip | 任意近期版本 | 建议 | 用 uv 更快 |
| PowerContext | 1.4.1（`powercontext[cli,server]`） | 是 | 任务状态保存与读取 |
| pylibseekdb | 1.4.0.post2 | 是 | 嵌入式 seekdb（数据库后端） |
| aiomysql | 0.3.2 | 是 | MySQL 协议客户端 |
| httpx | ≥0.28,<1 | 是 | 调用 PowerContext HTTP 接口 |
| pytest | ≥8,<9 | 是 | 业务工程测试 |
| MiniMax API Key | 见下 | 是 | 真实代码修改 |

本机实际运行的数据库引擎版本：`5.7.25-OceanBase seekdb-v1.4.0.0`
（证据见 `evidence/db/database-version.txt`）。

---

## 2. 安装

### 2.1 Python 依赖

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r minimax/requirements.txt
```

`minimax/requirements.txt` 只包含上下文管理模块自身的依赖；
PowerContext 单独安装：

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
# 或者：python -m pip install "powercontext[cli,server]==1.4.1"
```

### 2.2 嵌入式 seekdb 平台支持

`pylibseekdb` 官方提供 `cp311/cp312` 的 `manylinux`、`macosx`、`win_amd64` wheel。

* Windows x64 + Python 3.12 → `pylibseekdb-1.4.0.post2-cp312-abi3-win_amd64.whl`
* 如果目标平台没有 wheel，改用 OceanBase 服务端：跳过 seekdb 安装，
  设置 `SDDCTX_DB_BACKEND=oceanbase` 与 `SDDCTX_OCEANBASE_DSN`（见 §3）。

---

## 3. 配置

复制占位配置并按需修改：

```bash
cp minimax/.env.example minimax/.env
```

必填项：

| 变量 | 说明 |
| --- | --- |
| `SDDCTX_MINIMAX_API_KEY` | **MiniMax API Key**（真实密钥请用环境变量注入，不要写进 `.env` 或提交到仓库） |
| `SDDCTX_POWERCONTEXT_SCOPE` | PowerContext Scope ID，可用 `python -m sddctx state init-scope` 自动创建后填入 |

可选项（均已在 `.env.example` 中给出默认值）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SDDCTX_PROJECT_ROOT` | `./minimax/business_project` | 业务工程根目录 |
| `SDDCTX_WORK_DIR` | `./minimax/.sddctx-run` | 运行目录（资料库数据与临时文件） |
| `SDDCTX_EVIDENCE_DIR` | `$SDDCTX_WORK_DIR/evidence` | 证据输出目录；交付物里的证据位于 `minimax/evidence` |
| `SDDCTX_DB_BACKEND` | `seekdb` | `seekdb` 或 `oceanbase` |
| `SDDCTX_SEEKDB_PATH` | `$SDDCTX_WORK_DIR/seekdb` | 嵌入式 seekdb 数据目录 |
| `SDDCTX_OCEANBASE_DSN` | 空 | `mysql://user:pass@host:2881/database` |
| `SDDCTX_OCEANBASE_TABLE_PREFIX` | `sdd` | 表名前缀 |
| `SDDCTX_POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `SDDCTX_POWERCONTEXT_SCOPE_KEY` | `sddctx-minimax-oceanbase-scope` | 创建 Scope 时的幂等键；换一个值即可得到一个全新的 Scope（从空状态重做） |
| `SDDCTX_POWERCONTEXT_TOKEN` | 空 | Server 开启鉴权时填写 |
| `SDDCTX_MINIMAX_BASE_URL` | `https://api.minimaxi.com/anthropic` | MiniMax 端点（见下表） |
| `SDDCTX_MINIMAX_MODEL` | `MiniMax-M2.7` | 模型名（`MiniMax-M2.7` / `MiniMax-M3` / `MiniMax-M3.1-Flash` 等） |
| `SDDCTX_MINIMAX_PROTOCOL` | `anthropic` | `anthropic`（`x-api-key`）或 `openai`（`Authorization: Bearer`） |

MiniMax 端点（请与账号所在区域一致）：

| 区域 | Anthropic 兼容（推荐） | OpenAI 兼容 |
| --- | --- | --- |
| 中国大陆 | `https://api.minimaxi.com/anthropic` | `https://api.minimaxi.com/v1` |
| 国际 | `https://api.minimax.io/anthropic` | `https://api.minimax.io/v1` |

> 仓库中不含任何真实凭据。`.env.example` 全部为占位值。

---

## 4. 初始化数据

### 4.1 启动 PowerContext Server（任务状态后端）

```bash
# 在仓库根目录
powercontext server run
```

默认使用本地 SQLite 持久化，监听 `http://127.0.0.1:8000`。
另开一个终端验证：

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/live
```

> **踩坑：复用旧 SQLite 库会报 `unknown_baseline`。**
> PowerContext 的默认库位于 `%LOCALAPPDATA%\powercontext\powercontext.db`，
> 如果该库是旧版本 schema 留下的，启动/写入接口会拒绝（`unknown_baseline`）。
> 解决办法是给 Server 指定一个**全新的** SQLite 文件：
>
> ```bash
> export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/.artifacts/powercontext-fresh.db"
> powercontext server run
> ```
>
> 另外：SQLite 后端下 `--role` 必须为 `all`（`--role api` 会直接校验失败）；
> `server` 子命令依赖 `powercontext[builtin]` 里的 `jieba`，缺它会让 CLI 把 `server`
> 隐藏掉（表现为 “no such command: server”）。

### 4.2 创建 Scope（PowerContext 的隔离边界）

```bash
cd minimax
python -m sddctx state init-scope
```

把打印出来的 `scope_id` 写入 `.env` 的 `SDDCTX_POWERCONTEXT_SCOPE`。

`init-scope` 使用幂等键 `SDDCTX_POWERCONTEXT_SCOPE_KEY`（默认
`sddctx-minimax-oceanbase-scope`）创建 Scope：**同一个键会复用同一个 Scope**，
想从完全空的状态重跑，就换一个新的幂等键：

```bash
export SDDCTX_POWERCONTEXT_SCOPE_KEY="sddctx-minimax-oceanbase-$(date +%s)"
python -m sddctx state init-scope
```

### 4.3 初始化并导入资料库（seekdb / OceanBase）

```bash
cd minimax
python -m sddctx init      # 建表
python -m sddctx import    # 导入规格、代码与测试，并重建倒排索引
```

预期输出（首次导入）：

```json
{
  "backend": "seekdb",
  "imported": { "specs": 13, "code_symbols": 21, "tests": 29, "source_files": 15 },
  "changes": { "specs_inserted": 13, "code_inserted": 21, "tests_inserted": 29, "tokens_written": 1783 }
}
```

> 资料库是**空状态起步**的：`init` 只建表，`import` 才写入数据。

---

## 5. 调用入口

所有入口都是 `python -m sddctx <子命令>`（在 `minimax/` 目录下执行）。

| 子命令 | 作用 |
| --- | --- |
| `init` | 初始化资料库（建表） |
| `import` | 导入规格 / 代码 / 测试资料 |
| `search --query "..."` | 在数据库中检索资料（真实 SQL 查询） |
| `context --task "..." [--task-id ID] [--json] [--out FILE]` | 生成开发上下文 |
| `prompt --task "..." [--task-id ID] [--out FILE]` | 只组装并落盘交给 MiniMax 的提示词（不调用模型） |
| `state init-scope` | 创建并打印 PowerContext Scope |
| `state save --task-id ID --file state.json` | 保存任务状态到 PowerContext |
| `state show --task-id ID` | 从 PowerContext 读取任务状态 |
| `patch --task "..." [--task-id ID] [--dry-run]` | 调用 MiniMax 完成代码修改并落盘 |
| `doctor` | 检查数据库、PowerContext、MiniMax 配置 |

全局参数：`--env-file`（`.env` 路径）、`--project`（业务工程根目录）、`--work-dir`（运行目录）。

`patch` 的三个调用方式：

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| 真实 API 调用 | `python -m sddctx patch --task "..." --task-id TASK-001` | 需要 `SDDCTX_MINIMAX_API_KEY`；提示词由数据库检索出的上下文组装，模型响应经 `git apply` 落盘 |
| 只落盘提示词 | `python -m sddctx prompt --task "..." --task-id TASK-001 --out prompt.md` | 不调用模型，便于人工检查上下文质量 |
| 复现已留档响应 | `python -m sddctx patch --task "..." --task-id TASK-001 --response-file resp.md --response-model "MiniMax-M3.1-Flash"` | `--response-file` 指向一份已留档的模型响应，**不会**发起网络请求；`--response-model` 标注该响应由哪个模型产出，会如实写进调用记录 |

> `--response-file` 走的是**与真实 API 调用完全相同的应用路径**（同一套 `git apply`
> + 容错回退逻辑），差别只在于响应来源。调用记录里会标注来源，
> 不会把已留档响应伪装成实时 API 调用。

示例：

```bash
cd minimax
python -m sddctx search --query "阶梯满减 门槛 tiered_discount_rate" --limit 10
python -m sddctx context --task "实现阶梯满减折扣" --task-id TASK-001 --out context.md
python -m sddctx prompt --task "实现阶梯满减折扣" --task-id TASK-001 --out prompt.md
python -m sddctx patch --task "实现阶梯满减折扣" --task-id TASK-001 --dry-run
python -m sddctx doctor
```

---

## 6. 测试命令

### 6.1 业务工程测试（验证样例）

```bash
cd minimax/business_project
python -m pytest tests -q
```

不需要安装本包（`tests/conftest.py` 会把 `src/` 加入 `sys.path`）。

### 6.2 带元数据的日志采集

```bash
python minimax/scripts/capture.py \
  --out minimax/evidence/logs/before-tests.log \
  --label "修改前：目标功能测试" \
  --cwd minimax/business_project \
  --repo . \
  -- python -m pytest tests -q
```

日志开头记录命令、退出码、起止时间与对应代码版本（Git commit + 工作区改动摘要）。

---

## 7. 端到端验证样例

```bash
cd minimax
python demo/run_demo.py --reset
```

脚本按顺序执行 `init → import → search → context → state save → context → patch → state show`，
每一步都是**独立进程**（因此“重启后读取状态”是真实成立的）。
各步骤日志落在 `minimax/.sddctx-run/logs/`。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--reset` | 先删除运行目录，从空状态重跑全流程 |
| `--skip-patch` | 跳过 MiniMax 修改步骤（只验证资料库与状态链路） |
| `--skip-state` | 跳过需要 PowerContext Server 在线的步骤 |
| `--response-file FILE` | 用一份已留档的模型响应驱动 patch 步骤（不发起网络请求） |
| `--response-model NAME` | 标注该响应的产出模型，会如实写进调用记录 |

本仓库交付的证据就是用下面这条命令跑出来的（响应来源见
`evidence/minimax/minimax-call-TASK-001.json` 的 `source` 字段）：

```bash
python demo/run_demo.py --reset \
  --response-file evidence/minimax/minimax-response-TASK-001.md \
  --response-model "MiniMax-M3.1-Flash"
```

---

## 8. 已知限制

1. 检索为**词元倒排索引 + 词频排序**，非向量语义检索；规格正文点名的标识符会被额外用于
   “规格 → 代码”定位，以弥补中英文表述差异造成的漏检。
2. 规格解析只识别 Markdown 二级标题 `## <规格编号> <标题>`；更高级标题会并入上一条目正文。
3. 代码与测试的符号抽取仅支持 Python（`ast`）。
4. `SDDCTX_MINIMAX_PROTOCOL=anthropic` 时请求头为 `x-api-key`；`openai` 时为
   `Authorization: Bearer`。请与所用端点保持一致。
5. MiniMax 补丁优先用 `git apply` 应用；失败时按上下文块做容错替换，仍失败则保留模型原始
   响应并返回非零退出码，**不会**写入半成品。
6. PowerContext 状态读取优先走 `/v1/memory/search`，未命中时回退到 Source 证据读取。
7. 嵌入式 seekdb 需要本地可写的 `SDDCTX_SEEKDB_PATH`；同一路径复用时数据保留，
   `python -m sddctx init` 不会清空已有数据（需要完全重来请删除该目录或使用 `--reset`）。
8. Windows 下 PowerContext Server 为实验性支持；本文的验证在 Windows 11 + Python 3.12.16 上完成。
