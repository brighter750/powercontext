# 部署与运行说明

本文是给评审的完整复现步骤：从零开始跑起 StudyFlow，并核对主要功能。
所有配置示例只用占位值，不包含任何真实密钥。

---

## 1. 系统环境

| 项 | 要求 | 本次验证使用的版本 |
| --- | --- | --- |
| 操作系统 | Windows / macOS / Linux | Windows 10（PowerShell 5.1） |
| Python | 3.11 或更高 | 3.12.16 |
| 包管理 | `uv`（推荐）或 `pip` | uv 0.9.x |
| PowerContext Server | 1.4.x | 契约 v1.4 |
| 浏览器 | 支持 ES Module 的现代浏览器 | Chromium / Edge |
| Node.js | **不需要** | — |

前端没有构建步骤，因此**不需要** Node.js、npm 或任何打包器。

---

## 2. 安装依赖

在**仓库根目录**（本项目位于 `qoder/` 子目录）执行：

```bash
uv sync
```

只想安装 StudyFlow 自己的依赖时：

```bash
cd qoder
uv pip install -e ".[dev]"
```

依赖清单（`qoder/pyproject.toml`）：

```text
fastapi>=0.115
uvicorn[standard]>=0.30
httpx>=0.27
pydantic>=2.7
pydantic-settings>=2.3
pytest>=8.0
pytest-asyncio>=0.23
```

---

## 3. 启动 PowerContext Server

### 3.1 安装 CLI

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext --version
```

或直接使用本仓库源码（仓库根目录已 `uv sync` 时）：

```bash
uv run powercontext server run
```

### 3.2 启动（推荐：使用交付的脚本）

```powershell
# Windows PowerShell 5.1
powershell -NoProfile -File qoder/scripts/start_powercontext.ps1 -Token "your-token" -OpenDashboard
```

脚本会：

* 使用独立数据库 `qoder/.artifacts/powercontext-studyflow.db`，不干扰本机其它实例；
* 设置 `ACCESS_MODE=enforced` 与 `POWERCONTEXT_SERVER_AUTH_TOKEN`；
* 只监听 `127.0.0.1`。

### 3.3 启动（手动）

```bash
export POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
export POWERCONTEXT_SERVER_HTTP_PORT=8765
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/qoder/.artifacts/powercontext-studyflow.db"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="your-token"
export POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
powercontext server run
```

> Windows 上如果用 `Tee-Object` 落盘，服务端日志会是 UTF-16LE；
> `qoder/scripts/export_server_log.py` 会自动识别编码，不受影响。

### 3.4 验证就绪

```bash
curl -H "Authorization: Bearer your-token" http://127.0.0.1:8765/health/ready
```

期望 `"status": "ready"` 且 `"access_mode": "enforced"`。

不带令牌应当返回 **401**：

```bash
curl -i http://127.0.0.1:8765/v1/server-info
```

---

## 4. 配置 StudyFlow 后端

### 4.1 创建环境变量文件

```bash
cp qoder/.env.example qoder/.env
```

Windows PowerShell：

```powershell
Copy-Item qoder/.env.example qoder/.env
```

> `qoder/.env` 已被 `qoder/.gitignore` 忽略，**不会**被提交到仓库。

### 4.2 必填项

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `STUDYFLOW_POWERCONTEXT_BASE_URL` | PowerContext 地址 | `http://127.0.0.1:8765` |
| `STUDYFLOW_POWERCONTEXT_TOKEN` | 与服务端一致的令牌 | `your-token` |

### 4.3 可选项（AI 模型服务）

| 变量 | 说明 |
| --- | --- |
| `STUDYFLOW_AI_API_BASE` | 模型服务地址 |
| `STUDYFLOW_AI_API_KEY` | 模型服务凭据 |
| `STUDYFLOW_AI_MODEL` | 模型名 |
| `STUDYFLOW_AI_API_STYLE` | `openai`（`/chat/completions`）或 `anthropic`（`/messages`） |

**不配置也可以运行**：学习记录、薄弱分析、复习计划、记忆中心都由真实练习记录驱动。
只有 AI 助手的回答与练习题生成会明确提示"未配置"，不会返回伪造内容。

---

## 5. 配置 AI 模型服务

### 5.1 Anthropic 兼容协议

```dotenv
STUDYFLOW_AI_API_BASE=https://api.minimaxi.com/anthropic
STUDYFLOW_AI_MODEL=MiniMax-M2.7
STUDYFLOW_AI_API_STYLE=anthropic
STUDYFLOW_AI_API_KEY=replace-with-your-own-key
```

请求会发往 `POST {base}/messages`，使用 `x-api-key` 与 `anthropic-version` 头。

### 5.2 OpenAI 兼容协议

```dotenv
STUDYFLOW_AI_API_BASE=https://api.openai.com/v1
STUDYFLOW_AI_MODEL=gpt-4o-mini
STUDYFLOW_AI_API_STYLE=openai
STUDYFLOW_AI_API_KEY=replace-with-your-own-key
```

请求会发往 `POST {base}/chat/completions`，使用 `Authorization: Bearer` 头。

### 5.3 验证配置生效

```bash
curl http://127.0.0.1:8080/api/system/status
```

期望 `"ai_configured": true`。

### 5.4 配置后补跑验收样例

```bash
python qoder/scripts/verify_scenarios.py
```

此时样例 A / B / C 会给出完整结果（原先标记为"需要配置 AI 模型后复测"）。

---

## 6. 启动 StudyFlow 后端

```powershell
# Windows
powershell -NoProfile -File qoder/scripts/run_backend.ps1
```

```bash
# bash
cd qoder/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

验证：

```bash
curl http://127.0.0.1:8080/api/health
# {"status":"ok","service":"studyflow"}
```

打开 <http://127.0.0.1:8080> 即可使用；
API 交互文档在 <http://127.0.0.1:8080/docs>。

---

## 7. 初始化演示数据（可选）

```bash
python qoder/scripts/seed_demo.py
```

会写入一位演示学习者（数据库系统课程、6 条练习记录），
方便立即看到薄弱分析、复习计划与记忆中心的效果。

重置为干净状态：

```powershell
powershell -NoProfile -File qoder/scripts/reset_demo.ps1
```

> 重置脚本只删除 `qoder/.artifacts/` 下的 StudyFlow 专用数据库与日志，
> 不会触碰本机其它 PowerContext 实例的数据。

---

## 8. 测试命令

```bash
cd qoder
pytest tests -q
```

分层运行：

```bash
pytest tests/test_unit_domain.py -q                  # 纯单元，无需任何服务
pytest tests/test_mock_service.py -q                 # 内存替身，无需任何服务
pytest tests/test_integration_powercontext.py -q     # 需要真实 PowerContext Server
pytest tests/test_api_integration.py -q              # 需要 PowerContext + 运行中的后端
```

指向特定的 Server：

```bash
STUDYFLOW_TEST_POWERCONTEXT_URL=http://127.0.0.1:8765 \
STUDYFLOW_TEST_POWERCONTEXT_TOKEN=your-token \
STUDYFLOW_TEST_API_URL=http://127.0.0.1:8080 \
pytest tests -q
```

没有可用的 Server 时，真实集成用例会**跳过**而不是伪装通过。

执行赛题验收样例并生成证据：

```bash
python qoder/scripts/verify_scenarios.py
python qoder/scripts/export_server_log.py
```

---

## 9. 环境变量总表

| 变量 | 默认值 | 必填 | 说明 |
| --- | --- | --- | --- |
| `STUDYFLOW_POWERCONTEXT_BASE_URL` | `http://127.0.0.1:8765` | 是 | PowerContext 地址 |
| `STUDYFLOW_POWERCONTEXT_TOKEN` | 无 | 是 | Bearer 令牌；服务端未鉴权时填非空占位符 |
| `STUDYFLOW_POWERCONTEXT_TIMEOUT` | `30` | 否 | 单次请求超时（秒） |
| `STUDYFLOW_POWERCONTEXT_SCOPE_KIND` | `studyflow.learner_id` | 否 | Scope 外部引用类型 |
| `STUDYFLOW_POWERCONTEXT_INTEGRATION` | `studyflow` | 否 | scope binding 的 integration |
| `STUDYFLOW_POWERCONTEXT_BINDING_KIND` | `learner` | 否 | scope binding 的 kind |
| `STUDYFLOW_AI_API_BASE` | 无 | 否 | 模型服务地址 |
| `STUDYFLOW_AI_API_KEY` | 无 | 否 | 模型服务凭据 |
| `STUDYFLOW_AI_MODEL` | 无 | 否 | 模型名 |
| `STUDYFLOW_AI_API_STYLE` | `openai` | 否 | `openai` / `anthropic` |
| `STUDYFLOW_AI_TIMEOUT` | `120` | 否 | 模型请求超时（秒） |
| `STUDYFLOW_AI_MAX_TOKENS` | `2048` | 否 | 单次最大输出 token |
| `STUDYFLOW_AI_TEMPERATURE` | `0.4` | 否 | 采样温度 |
| `STUDYFLOW_DEFAULT_USER_ID` | `student_demo_001` | 否 | 首次打开时的学习者 ID |

所有凭据**只存在于后端进程环境**，不会下发到浏览器。

---

## 10. 故障排查

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 页面能打开但所有数据为空 | PowerContext 未启动或地址不对 | 检查 `/api/system/status` 的 `powercontext_ok` |
| 界面提示「PowerContext 不可用」 | Server 未就绪 / 端口不通 | 看 PowerContext 启动日志；确认 `STUDYFLOW_POWERCONTEXT_BASE_URL` |
| 界面提示「AI 模型未配置」 | 未配置模型服务 | 见 §5；这是**设计行为**，不是故障 |
| `401 Unauthorized` | 令牌不一致或未带令牌 | 两端使用同一个 `POWERCONTEXT_SERVER_AUTH_TOKEN` 与 `STUDYFLOW_POWERCONTEXT_TOKEN` |
| `403 Forbidden` | 凭据有效但无权访问该 Scope | 确认 `scope_id` 属于当前学习者 |
| `504 Gateway Timeout` | PowerContext 响应慢 | 调大 `STUDYFLOW_POWERCONTEXT_TIMEOUT`；检查 Server 负载 |
| `503 Service Unavailable` | 连不上 Server | 确认 Server 进程存活、端口未被占用 |
| Server 启动报 `recovery_required` | 删库时漏删 WAL/SHM/迁移状态 | 用 `reset_demo.ps1` 统一清理后重启 |
| PowerContext 客户端拒绝明文 HTTP | 服务地址不是回环地址 | 改用 `127.0.0.1`/`localhost`，或部署到 HTTPS 之后 |
| PowerShell 脚本报"缺少字符串终止符" | PowerShell 5.1 读取无 BOM 的 UTF-8 脚本 | 交付的 `.ps1` 已带 UTF-8 BOM；自行修改后请保存为 UTF-8 with BOM |
| 端口被占用 | 8765 / 8080 已被使用 | `start_powercontext.ps1 -Port 8766`、`run_backend.ps1 -Port 8081` |
| 前端打开但控制台报模块加载失败 | 用 `file://` 直接打开了 `frontend/index.html` | 必须通过后端地址 <http://127.0.0.1:8080> 访问 |

---

## 11. 目录与产物说明

| 路径 | 内容 | 是否提交 |
| --- | --- | --- |
| `qoder/backend/` | 后端源码 | 是 |
| `qoder/frontend/` | 前端源码 | 是 |
| `qoder/tests/` | 测试 | 是 |
| `qoder/scripts/` | 启停与验证脚本 | 是 |
| `qoder/docs/` | 文档 | 是 |
| `qoder/evidence/` | 真实运行产生的证据 | 是 |
| `qoder/assets/screenshots/` | 界面截图 | 是 |
| `qoder/site/` | Qoder Sites 站点源码 | 是 |
| `qoder/.env` | 本机环境变量（含真实凭据） | **否**（已 gitignore） |
| `qoder/.artifacts/` | 数据库与日志 | **否**（已 gitignore） |
