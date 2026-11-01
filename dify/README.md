# 忘忧酒肆 · 记忆掌柜

> 一间开在旧渡口的小酒肆。掌柜阿墨记性极好：你爱喝什么酒、欠着哪句话没说完、上回把酒壶藏进了柴房——她都记得。
> 换个会话再来，她照样认得你。

一个用 **Dify + PowerContext** 做的互动角色扮演应用。应用的全部"记性"由 PowerContext 持久化：
每一轮互动结束，掌柜会把这一轮值得记住的事写进这位旅人的记忆册；下一轮开口前，先把记忆册读回来。
**换浏览器、开新会话，记忆还在。**

---

## 1. 玩法

| 步骤 | 你做什么 | 掌柜做什么 |
| --- | --- | --- |
| 1 | 在开场填一个**旅人代号**（比如"阿福"） | 这个代号对应一本专属记忆册，第一次见面是生客 |
| 2 | 说自己的名字、喜好、糗事、赊账 | 边聊边把值得记的事写进册子，每轮最多补一条 |
| 3 | 关掉页面，**开一个新会话**，用同一个代号回来 | 翻开册子，点名你上回说过的事、欠着的账 |
| 4 | 交代一件新的事 | 册子里多一条，旧的还在 |

回答末尾的 **【记忆册 · 你的代号】** 是直接从 PowerContext 读回来的当前册子内容，
每条都带着类别（事实 / 性格 / 承诺 / 糗事 / 见闻），最后一行是记忆册在 PowerContext 里的 scope id——
对着这个 id 就能在 PowerContext 里查到一模一样的数据。

**为什么好玩**：玩的就是"她到底记不记得我"。你可以骗她、赖账、再回来赖账翻旧账；
换个代号再进，她对你一无所知（这本册子是按代号隔离的）。

---

## 2. 技术选型与分工

| 组件 | 承担什么 | 在哪里体现 |
| --- | --- | --- |
| **Dify**（Chatflow / advanced-chat） | 应用核心：输入处理、条件判断、模型调用、结果输出 | `apps/innkeeper-chatflow.yml` 的 13 个节点 |
| **PowerContext** | **唯一**的上下文与记忆服务：建册、读取、写入、复核 | DSL 中 4 个 HTTP Request 节点，全部直连 PowerContext HTTP API |
| 模型服务 | 生成掌柜的话 | DSL 的 LLM 节点，走 Dify 的 `langgenius/openai_api_compatible` 插件，示例接 MiniMax |

**本方案不使用 seekdb**：不部署、不作为 Dify 数据库、不引入任何 seekdb 依赖、配置或代码。
Dify 自带的数据库保持默认，PowerContext 自己用 SQLite（或 OceanBase）持久化。
仓库内 `dify/` 目录下不存在任何 seekdb 引用，`scripts/check_dsl.py` 也会主动检查这一点。

### 工作流里的四个 PowerContext 调用

| DSL 节点 | PowerContext 接口 | 方法 | 作用 |
| --- | --- | --- | --- |
| 建立记忆册 Scope | `POST /v1/scopes` | POST | 用幂等键 `dify-innkeeper-<旅人代号>` 建（或复用）这本册子 |
| 读取记忆册 | `POST /v1/memory/entries/list` | POST | 读出册子当前全部有效记忆 |
| 写入记忆册 | `POST /v1/memory/remember` | POST | 把本轮新记忆写成 Atomic Memory |
| 复核记忆册 | `POST /v1/memory/entries/list` | POST | 写完再读一遍，回给用户看的册子就是库里的真实内容 |

> 接口契约：PowerContext HTTP API v1.4（`GET /v1/server-info` 可查 `api_contract_version`）。
> 完整契约见仓库 `openapi/powercontext.yaml`。

---

## 3. 目录结构

```
dify/
├── apps/
│   └── innkeeper-chatflow.yml      # Dify Chatflow DSL（导入这个文件）
├── scripts/
│   ├── check_dsl.py                # DSL 结构校验器（导入前先跑一遍）
│   ├── dsl_runner.py               # 本地参考执行器：按 DSL 节点顺序真实执行
│   ├── verify_cross_session.py     # 跨会话记忆验证脚本
│   ├── export_server_log.py        # 从 Server 日志导出记忆操作记录
│   └── reset_state.py              # 清空演示用的 PowerContext 本地状态
├── evidence/
│   └── cross-session/              # 真实运行证据（请求、响应、服务端日志、校验结论）
└── README.md                       # 本文件
```

---

## 4. 环境要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Dify | 1.x（DSL `version: 0.1.5`） | 自建（docker compose）或 Dify 云版均可 |
| PowerContext Server | 1.4.1（HTTP API 契约 v1.4） | 独立部署，监听 `http://127.0.0.1:8000`（默认） |
| Python | 3.11+ | 仅运行 `scripts/` 下的辅助脚本时需要；Dify 与 PowerContext 本身不依赖本仓库的 Python 环境 |
| 模型服务 | 任意 OpenAI 兼容服务 | 示例：MiniMax `https://api.minimaxi.com/v1`，模型 `MiniMax-M2.7` |

不需要 Embedding / Rerank 服务：记忆的存取用的是 PowerContext 的 Atomic Memory 显式读写接口，不走向量检索。

---

## 5. 部署步骤

### 5.1 启动 PowerContext Server

方式一：安装已发布版本（推荐）

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext server run          # 默认 http://127.0.0.1:8000，本地 SQLite 持久化
```

> `powercontext[cli,server]` 不含 seekDB 扩展（那是可选的 `[seekdb]` extra），本方案也不需要它。

方式二：用本仓库源码（开发调试时更方便）

```bash
cd powercontext
uv sync
uv run powercontext server run
```

**建议给演示准备一个独立的库文件**，避免和本机其它演示互相干扰：

```bash
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/.artifacts/powercontext-dify.db"
powercontext server run
```

就绪检查：

```bash
curl --fail http://127.0.0.1:8000/health/ready
# {"status":"ready","checks":{"runtime":"ready","database":"ready",...}}
```

> 注意：删库重来时，要连同 `<库名>-wal`、`<库名>-shm`、`<库名>.pc-migration-state`（目录）、
> `.<库名>.cursor-key` 一起删掉，否则 Server 会以 `recovery_required` 拒绝启动。
> `scripts/reset_state.py` 已经把这套清理逻辑包好了。

### 5.2 导入 DSL

1. Dify → 工作室 → 创建应用 → **导入 DSL 文件** → 选择 `dify/apps/innkeeper-chatflow.yml`。
2. 导入后先跑一次结构自检（可选，但推荐）：
   ```bash
   python dify/scripts/check_dsl.py dify/apps/innkeeper-chatflow.yml
   ```
3. 检查 13 个节点全部导入成功、连线完整（DSL 里有 13 个节点、13 条边，无悬空引用）。

### 5.3 配置模型

DSL 里 LLM 节点用的是 Dify 官方插件 `langgenius/openai_api_compatible`：

1. 插件市场安装 **OpenAI-API-compatible**（`langgenius/openai_api_compatible`）。
2. 在该插件里添加模型，填写：
   - API Base URL：`https://api.minimaxi.com/v1`（国内）/ `https://api.minimax.io/v1`（国际）
   - API Key：你的 MiniMax 开放平台 Key（**占位符，不要提交到仓库**）
   - 模型名称：**必须与 DSL 里的 `model.name` 完全一致**，默认是 `MiniMax-M2.7`
     （可用 `MiniMax-M2.5` / `MiniMax-M3` 等，导入后在这里改一次即可）
3. 回到工作流，把 LLM 节点的模型换成你刚添加的那个。

不想用 MiniMax 也可以：任何 OpenAI 兼容服务（DeepSeek、Kimi、通义等）都行，只要改 base URL、模型名和 Key。

### 5.4 配置环境变量

DSL 声明了两个环境变量，在应用的「环境变量」里改：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_BASE_URL` | `http://host.docker.internal:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_TOKEN` | `disabled` | 访问令牌。**Server 未开启鉴权时填任意非空占位符**（空值会形成非法 HTTP 头）；开启了鉴权就填真实 Token |

**网络条件**：Dify 跑在容器里时要用 `host.docker.internal:8000` 访问宿主机上的 PowerContext；
Dify 和 PowerContext 都在同一台宿主机、Windows/macOS/Linux 都适用。
如果 PowerContext 也跑在容器里，把地址换成容器服务名（例如 `http://powercontext:8000`）并确保两个容器在同一网络。

> 鉴权关闭时，PowerContext 会忽略任何 `Authorization` 头，所以 `POWERCONTEXT_TOKEN=disabled` 可以直接用。
>
> 如果要给 Server 开鉴权，在**服务端**设置 `POWERCONTEXT_SERVER_AUTH_TOKEN`（同一个值填进 Dify 的
> `POWERCONTEXT_TOKEN`），客户端就会带上 `Authorization: Bearer <token>`。

### 5.5 跑起来

打开应用 → 在开场表单填**旅人代号** → 开始对话。第一句建议：

```
掌柜好，我叫阿福，最爱喝桂花酿。以后我大概会常来。
```

---

## 6. 工作流结构

```
开始(旅人代号)
  └─ 准备旅人档案            [Code]      清洗代号 → 幂等键 / 册子标题
      └─ 建立记忆册 Scope      [HTTP]     POST /v1/scopes（幂等，同代号复用同一本）
          └─ 取回记忆册 ID      [Code]     解析 scope_id
              └─ 读取记忆册     [HTTP]     POST /v1/memory/entries/list
                  └─ 整理摘要   [Code]     册子 → 提示词摘要；判新客/熟客
                      └─ 阿墨开口 [LLM]    只看"摘要 + 本轮这句话"
                          └─ 拆开掌柜的话 [Code]  正文 / 要落库的那一条
                              └─ 要不要记进记忆册 [IF/ELSE]
                                  ├─ true  → 写入记忆册 [HTTP] POST /v1/memory/remember
                                  └─ false → （跳过写入）
                                          └─ 复核记忆册 [HTTP] POST /v1/memory/entries/list
                                              └─ 渲染最终回复 [Code]
                                                  └─ 直接回复 [Answer]
```

几个刻意的设计：

- **LLM 节点关闭了会话短期记忆（`memory.window.enabled: false`）**。
  模型每轮只看两样东西：PowerContext 读回来的记忆册摘要，和这一轮旅人说的话。
  这样"跨会话还记得"这件事不可能是 Dify 自带的短期上下文冒充的——它只能来自 PowerContext。
- **写入请求体由 Code 节点预序列化**（`remember_payload` 是完整 JSON 字符串），
  避免中文、引号、换行把 JSON 弄坏；HTTP 节点直接把它当 body 发出去。
- **写入后会再读一次**（复核记忆册），所以回给用户的册子永远等于库里此刻的状态。
- **纯回忆轮不会重复落库**：模型判断"这一轮没有新事实"时 `remember=false`，
  IF/ELSE 直接跳过写入，册子不会越滚越脏。

---

## 7. 标识怎么关联

| 概念 | 取值 | 说明 |
| --- | --- | --- |
| 旅人代号 | 用户在开场表单填的 `player_id` | 人机接口上的玩家标识 |
| 幂等键 | `dify-innkeeper-<旅人代号>`（代号只保留字母/数字/中文/`-`/`.`） | 传给 `POST /v1/scopes` 的 `idempotency_key` |
| 记忆册 | PowerContext Scope，`scope_id` 形如 `scp_xxx` | **一人一册**，由幂等键保证同一代号永远拿到同一个 scope |
| 一条记忆 | PowerContext Atomic Memory，`artifact_id` 形如 `art_xxx` | 类别 `kind` 取 `fact`/`trait`/`promise`/`prank`/`story` |
| Dify 会话 | Dify 自己的 conversation | **不参与记忆关联**；换会话不影响记忆册 |

同一个代号 ⇒ 同一个 scope ⇒ 同一本记忆册；不同代号 ⇒ 不同 scope，互不可见。

---

## 8. 记忆读写流程

一轮互动的完整闭环：

1. **读**：Code 节点算出幂等键 → `POST /v1/scopes` 拿到（或复用）`scope_id` →
   `POST /v1/memory/entries/list` 读出册子当前内容。
2. **用**：Code 节点把册子压成摘要，Code 节点输出"新客/熟客"状态，LLM 节点据此开口。
3. **写**：LLM 在正文后给出一段 JSON（`remember` / `kind` / `text`）→
   Code 节点拆出正文与待写条目并预序列化请求体 → IF/ELSE 判断是否要写 →
   `POST /v1/memory/remember` 写入 Atomic Memory。
4. **复核**：`POST /v1/memory/entries/list` 再读一次，渲染成回复里的【记忆册】。

---

## 9. 验证样例（跨会话记忆）

**初始条件**：一个全新的旅人代号 `阿福`（在干净 PowerContext 上，册子是空的）。

### 会话 1 —— 交代一件事

- 输入：`掌柜好，我叫阿福，最爱喝桂花酿。以后我大概会常来。`
- 预期：掌柜以生客口吻招呼；回复末尾出现【记忆册 · 阿福】，里面有 1 条刚写进去的记忆。
- 检查点：`POST /v1/memory/entries/list` 能读回这条记忆。

### 结束会话 1

关闭会话 / 新开一个会话。**不要重复说桂花酿。**

### 会话 2 —— 只问，不说

- 输入：`掌柜，我上回说过我喜欢喝什么酒来着？`
- 预期：掌柜直接说出"桂花酿"，并翻起旧账；册子里仍然只有 1 条（没重复记）。
- 检查点：这句话里没有"桂花酿"三个字，回答里却出现了——只可能来自 PowerContext。

### 会话 3 —— 交代一件新的事

- 输入：`对了掌柜，我这次要赊一坛女儿红走，下月初还你钱，别记错了。`
- 预期：掌柜记下这条新账目；册子变成 2 条，旧的"桂花酿"还在。

### 反向对照 —— 换一个代号

- 用 `路人乙` 开新会话，输入同一句 `掌柜，我上回说过我喜欢喝什么酒来着？`
- 预期：掌柜说"没记过您爱喝什么酒"，册子是空的。

**恢复初始状态**：换一个新的旅人代号即可得到一本全新的册子；
要连 PowerContext 库一起清空，用 `python dify/scripts/reset_state.py --database-url <你的库 URL>`（需先停掉 Server）。

---

## 10. 不装 Dify 也能复现（本地参考执行器）

`scripts/dsl_runner.py` 是一个按 DSL 节点顺序执行的最小解释器：它直接读 `apps/innkeeper-chatflow.yml`，
用节点里声明的 URL、请求体、提示词和代码，真实调用 PowerContext 与模型服务，并把每一步的请求/响应记录下来。
**它不是 Dify**，但因为读的是同一份 DSL，所以证据不会和提交的工作流漂移。

```bash
# 1) 起 PowerContext
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/.artifacts/powercontext-dify.db"
powercontext server run

# 2) 配模型（任选其一）
export INNKEPER_LLM_BASE_URL="https://api.minimaxi.com/v1"     # OpenAI 兼容
export INNKEPER_LLM_PROTOCOL="openai"
export INNKEPER_LLM_MODEL="MiniMax-M2.7"
export INNKEPER_LLM_API_KEY="<你的 Key>"

# 3) 跑一轮
python dify/scripts/dsl_runner.py \
  --player-id "阿福" \
  --message "掌柜好，我叫阿福，最爱喝桂花酿。" \
  --trace trace.json

# 4) 跑整套跨会话验证（每轮都是全新进程）
python dify/scripts/verify_cross_session.py --player-id "阿福" --evidence-dir dify/evidence/cross-session
```

`verify_cross_session.py` 的每一轮都会**新起一个子进程**，所以跨会话能记住的唯一来源就是 PowerContext。

---

## 11. 证据

`evidence/cross-session/` 里是真实运行记录：

| 文件 | 内容 |
| --- | --- |
| `verification-summary.json` | 五项检查的结论、三个会话的册子快照、对照组回答 |
| `session-1-turn-1.json` | 会话 1 完整轨迹：建册请求/响应、读册请求/响应、模型提示词与回复、写入请求/响应、复核读册 |
| `session-2-turn-2.json` | 会话 2 完整轨迹（新进程，读回册子并作答） |
| `session-3-turn-3.json` | 会话 3 完整轨迹（新进程，写入新记忆） |
| `control-other-player.json` | 反向对照：另一个代号的完整轨迹 |
| `powercontext-server-log.txt` | 从 PowerContext Server 自己的 JSON 日志里筛出的记忆/Scope 操作记录（含 request_id、耗时、状态码） |

每条轨迹都含 HTTP 节点的真实请求体与服务端真实响应体，可与
`powercontext-server.log` 里的 `request_id` 相互对照。

---

## 12. 已知限制

- **每个旅人代号对应一本册子**，册子不跨代号共享；这是刻意的隔离设计。
- 每轮最多落库 **一条** 记忆（掌柜一次只往册子上补一条）。一轮里同时交代好几件事时，
  它们会合并进同一条记忆的文本里。
- 记忆册渲染最多展示 50 条（`entries/list` 的 `limit`），再多需要分页，暂未实现。
- PowerContext 的 Atomic Memory 是"一条一个事实"的模型；本应用用 `kind` 区分
  事实/性格/承诺/糗事/见闻，并不去修改已写条目的内容——纠正一条旧记忆需要在 PowerContext 侧
  用 Artifact 内容替换接口（`If-Match`）处理，DSL 暂未开放这个入口。
- 本地验证环境没有 Docker，因此 **Dify 容器栈本身没有在提交方机器上启动过**；
  已完成的是：DSL 结构静态校验（`check_dsl.py` 全绿）、以及按同一份 DSL 逐节点执行的真实闭环
  （真实 PowerContext 读写 + 真实模型调用）。导入 Dify 后的界面体验请按第 5 节配置后自行验证。
