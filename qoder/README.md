# StudyFlow —— 具有长期记忆的 AI 学习教练

> Your AI Study Coach with Persistent Memory

StudyFlow 是一个面向学习者的 AI 学习助手。它的关键区别不是"能聊天"，而是**能记住**：
学习者的课程、薄弱知识点、历史练习、自评和复习进度都保存在
[PowerContext](https://github.com/oceanbase/powercontext) 中，跨会话、跨进程持续存在。

关闭应用再打开，同一个学习者 ID 不需要重新介绍自己——系统会从 PowerContext
读回长期记忆，基于这些记忆给出复习建议并解释"为什么建议复习这个"。

---

## 目录

- [解决什么问题](#解决什么问题)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [PowerContext 在项目中的实际作用](#powercontext-在项目中的实际作用)
- [快速开始](#快速开始)
- [体验方式](#体验方式)
- [测试方式](#测试方式)
- [项目结构](#项目结构)
- [文档](#文档)

---

## 解决什么问题

学习记录天然是**跨会话**的，但绝大多数 AI 聊天工具是**会话内**的。真实痛点：

| 痛点 | StudyFlow 的做法 |
| --- | --- |
| 学习记录分散在不同笔记里，无法追踪 | 每次练习写入 PowerContext，形成权威的学习事件日志 |
| 不知道自己到底哪里薄弱 | 按练习正确率 + 自评 + 时间衰减计算掌握度，识别反复出错的知识点 |
| 复习计划没有针对性 | 每条建议都引用真实历史（"累计练习 3 次、答错 2 次…"），没有数据就不给解释 |
| 每次都要重新介绍自己的情况 | 学习档案、知识点状态、练习历史全部从 PowerContext 读回 |
| 做过的题和错题没被利用 | 旧练习保留在事件日志里，作为掌握度与进步趋势的依据 |

**一个刻意的设计约束**：一次答错不会让系统断定"你长期不会这个知识点"。
薄弱程度需要多次证据；只有一次正确、且自评良好的知识点不会被列为薄弱点。
这条规则有对应的单元测试保护（见 `tests/test_unit_domain.py`）。

---

## 核心功能

| 页面 | 能做什么 | 数据来自哪里 |
| --- | --- | --- |
| **学习概览 Dashboard** | 当前课程、目标、活动数、薄弱知识点、最近进展、今日复习建议 | PowerContext 读 |
| **AI 助手 AI Tutor** | 自然语言提问/口述学习情况；回答前先召回记忆，回答后把新信息写回记忆 | PowerContext 读 + 写 |
| **学习记录 Records** | 提交一次练习（题目、答案、对错、自评、笔记、卡点） | PowerContext 写 |
| **薄弱分析 Weakness** | 每个知识点的掌握度、错误次数、证据、判定原因 | PowerContext 读 |
| **复习计划 Review Plan** | 今日复习优先级、每项推荐原因、AI 生成的针对性练习 | PowerContext 读 + AI |
| **记忆中心 Memory Center** | 逐条展示 PowerContext 中真实存在的记忆条目、artifact ID、修订号 | PowerContext 读 |
| **设置 Settings** | 学习档案编辑、PowerContext 与 AI 服务状态 | PowerContext 读 |

记忆中心是刻意设计的：它把"PowerContext 真的参与了业务"变成**可逐条核对**的事实，
而不是一句声明。每一行都显示服务端返回的 `artifact_id` 和修订号。

---

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | Python 3.11+ / FastAPI / Uvicorn | 同时提供 JSON API 与前端静态资源 |
| PowerContext 接入 | `httpx` 直接调用 PowerContext HTTP API（契约 v1.4） | 全部调用集中在 `app/powercontext/client.py` |
| AI 模型 | 可插拔 Provider：OpenAI 兼容 / Anthropic 兼容 | 通过环境变量配置；未配置时明确报错，不伪造结果 |
| 前端 | 原生 HTML + CSS + ES Module，**无构建步骤** | 评审只需启动后端即可打开，无需 Node 工具链 |
| 测试 | pytest：单元 / Mock / 真实集成三层 | 真实集成测试直连 PowerContext Server |

前端不引入任何框架或打包器，是为了降低评审复现的门槛：不需要 `npm install`，
不需要构建，克隆下来就能跑。

---

## PowerContext 在项目中的实际作用

PowerContext 不是装饰性的依赖，它是这个产品**成立的前提**。具体承担四件事：

1. **长期记忆的存储** — 学习档案、知识点掌握状态、学习记录、薄弱点、复习计划，
   全部存为 PowerContext Atomic Memory 条目（`art_...` artifact）。
2. **跨会话读取** — 新会话通过学习者 ID 解析出同一个 Scope 并读回历史，
   进程重启、内存清空都不影响。
3. **记忆更新** — 新一次练习不是追加一条重复记忆，而是**修订**原有条目
   （`PUT /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}` + `If-Match`），
   修订号递增、历史版本保留。
4. **用户隔离** — 每个学习者 ID 在 PowerContext 中对应一个独立 Scope，
   并通过 scope binding 持久化映射。隔离由服务端 Scope 边界保证，不是前端隐藏。

实现细节见 [`docs/POWERCONTEXT_INTEGRATION.md`](docs/POWERCONTEXT_INTEGRATION.md)。

---

## 快速开始

### 环境要求

- Python 3.11 或更高
- PowerContext Server 1.4.x（可用 `powercontext[cli,server]` 安装）
- 可选：一个 OpenAI 兼容或 Anthropic 兼容的模型服务

### 1. 安装依赖

在仓库根目录（本项目位于 `qoder/`）：

```bash
uv sync
```

### 2. 准备环境变量

```bash
cp qoder/.env.example qoder/.env
# 编辑 qoder/.env，至少确认 PowerContext 地址与令牌
```

> `qoder/.env` 已被 `.gitignore` 忽略，不会提交真实密钥。

### 3. 启动 PowerContext Server

```powershell
# Windows PowerShell
powershell -NoProfile -File qoder/scripts/start_powercontext.ps1 -Token "your-token" -OpenDashboard
```

```bash
# bash / macOS / Linux
export POWERCONTEXT_SERVER_HTTP_PORT=8765
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/qoder/.artifacts/powercontext-studyflow.db"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="your-token"
powercontext server run
```

启动后确认就绪：

```bash
curl -H "Authorization: Bearer your-token" http://127.0.0.1:8765/health/ready
```

### 4. 启动 StudyFlow 后端

```powershell
powershell -NoProfile -File qoder/scripts/run_backend.ps1
```

打开 <http://127.0.0.1:8080> 即可使用。API 文档在 <http://127.0.0.1:8080/docs>。

### 5.（可选）写入演示数据

```bash
python qoder/scripts/seed_demo.py
```

会通过后端 API 写入一位演示学习者（数据库系统课程、6 条练习记录），
方便立刻看到薄弱分析、复习计划与记忆中心的效果。

---

## 体验方式

1. 打开 <http://127.0.0.1:8080>，左下角显示当前学习者 ID（默认 `student_demo_001`）。
2. **学习记录**页：填写知识点与练习结果 → 保存 → 观察掌握度与薄弱点变化。
3. **记忆中心**页：查看刚写入的记忆条目、`artifact_id` 与修订号。
4. **薄弱分析 / 复习计划**页：查看系统基于真实记录给出的判断与推荐原因。
5. **AI 助手**页（需先配置模型服务）：用自然语言提问，例如
   "我今天应该复习什么？"，观察它先召回记忆再回答，并把新信息写回记忆。
6. **验证用户隔离**：点击左下角学习者 ID 改成另一个 ID，所有页面数据清零，
   因为那是 PowerContext 中另一个独立 Scope。

---

## 测试方式

```bash
cd qoder
pytest tests -q
```

测试分三层，互不替代：

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 单元测试 | `tests/test_unit_domain.py` | 掌握度计算、薄弱判定、记忆信封解析、AI JSON 解码 |
| Mock 测试 | `tests/test_mock_service.py` | 用内存替身驱动真实 Service/Repository，验证 Scope 生命周期与"创建或修订"语义 |
| 真实集成测试 | `tests/test_integration_powercontext.py` | 直连真实 PowerContext Server：写入、读取、修订、检索、隔离、重启后持久化 |
| API 集成测试 | `tests/test_api_integration.py` | 通过 HTTP 驱动运行中的后端（需要后端已启动） |

真实集成测试在没有可用的 PowerContext Server 时会自动跳过，
不会伪装成通过。具体执行结果见 [`docs/TEST_CASES.md`](docs/TEST_CASES.md)。

---

## 项目结构

```text
qoder/
├── README.md                     # 本文件
├── DEVELOPMENT_LOG.md            # 开发过程记录
├── .env.example                  # 环境变量样例
├── pyproject.toml
├── backend/
│   └── app/
│       ├── main.py               # FastAPI 应用、异常映射、静态资源挂载
│       ├── config.py             # 配置（全部来自环境变量）
│       ├── schemas.py            # API 请求/响应模型
│       ├── memory_document.py    # 记忆信封格式（可解析的 key/value + 自然语言）
│       ├── api/routes.py         # HTTP 路由
│       ├── powercontext/
│       │   ├── client.py         # PowerContext HTTP 客户端（唯一的传输层）
│       │   └── repository.py     # Scope 生命周期 + 创建或修订语义
│       └── services/
│           ├── ai_service.py     # 可插拔 AI Provider
│           └── learning_service.py # 掌握度、薄弱点、复习计划等领域规则
├── frontend/                     # 无构建步骤的原生前端
│   ├── index.html
│   └── assets/{css,js}
├── scripts/
│   ├── start_powercontext.ps1    # 启动 PowerContext Server
│   ├── run_backend.ps1           # 启动 StudyFlow 后端
│   ├── seed_demo.py              # 写入演示数据
│   ├── verify_scenarios.py       # 执行验收样例 A–E 并生成证据
│   ├── read_memory_once.py       # 在全新进程中读回记忆（验收样例 E）
│   ├── export_server_log.py      # 导出 PowerContext 操作记录作为证据
│   └── reset_demo.ps1            # 清空本地演示状态
├── tests/                        # pytest：单元 / Mock / 真实集成
├── docs/
│   ├── ARCHITECTURE.md
│   ├── POWERCONTEXT_INTEGRATION.md
│   ├── TEST_CASES.md
│   ├── DEPLOYMENT.md
│   └── DEMO_GUIDE.md
├── evidence/                     # 真实运行产生的证据
│   ├── scenarios/                # 验收样例 A–E 的请求/响应记录
│   └── powercontext-server-operations.json
├── assets/screenshots/           # 应用界面截图
└── site/                         # Qoder Sites 展示站点
```

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 系统架构、职责划分、记忆读写流程、跨会话与用户隔离原理 |
| [`docs/POWERCONTEXT_INTEGRATION.md`](docs/POWERCONTEXT_INTEGRATION.md) | 部署方式、实际 API 调用、认证、更新方式、持久化验证、常见错误 |
| [`docs/TEST_CASES.md`](docs/TEST_CASES.md) | 验收样例 A–E 的实际执行过程与结果 |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | 环境要求、部署步骤、配置项、故障排查 |
| [`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md) | 演示脚本与讲解要点 |
| [`DEVELOPMENT_LOG.md`](DEVELOPMENT_LOG.md) | 开发过程记录与问题修复过程 |

---

## 项目链接

- 项目网站（Qoder Sites 发布后填写）：`待发布`
- 代码仓库：`https://github.com/brighter750/powercontext`（分支 `2026-competition-ecosystem`，目录 `qoder/`）
- PowerContext：`https://github.com/oceanbase/powercontext`

---

## 许可

Apache-2.0。本目录下的 StudyFlow 代码为参赛作品；
PowerContext 为 OceanBase 官方开源项目，通过 HTTP API 接入，未修改其源码。
