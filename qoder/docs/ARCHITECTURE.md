# StudyFlow 架构说明

## 1. 总体架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│  浏览器（原生 ES Module，无构建步骤）                                  │
│  Dashboard · AI Tutor · Records · Weakness · Review · Memory · Settings │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  同源 JSON API（无凭据暴露给前端）
┌──────────────────────────────▼──────────────────────────────────────┐
│  StudyFlow 后端（FastAPI）                                            │
│                                                                       │
│  app/api/routes.py        HTTP 层：参数校验、响应建模                  │
│  app/services/            领域层：掌握度、薄弱点、复习计划            │
│    learning_service.py      ↑ 唯一的领域规则来源                      │
│    ai_service.py            AI Provider（openai / anthropic）        │
│  app/powercontext/        记忆层：                                   │
│    repository.py            Scope 生命周期、创建或修订语义             │
│    client.py                HTTP 客户端（唯一的传输层）               │
│  app/memory_document.py   记忆信封格式（可解析字段 + 自然语言）        │
│  app/config.py            配置：全部来自环境变量，密钥不出后端         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  Authorization: Bearer <token>
┌──────────────────────────────▼──────────────────────────────────────┐
│  PowerContext Server（HTTP API 契约 v1.4）                             │
│  Scope（学习者隔离边界） · Atomic Memory（记忆条目） · Access Control  │
└─────────────────────────────────────────────────────────────────────┘
```

后端同时挂载 `frontend/` 静态资源，因此一个进程就能提供完整的应用，
评审不需要第二个服务。

---

## 2. 各层职责

### 2.1 HTTP 层 `app/api/routes.py`

只做三件事：解析查询参数、调用领域服务、把领域对象映射成响应模型。
不包含任何业务规则——例如"什么算薄弱知识点"绝不写在这里。

| 路由 | 方法 | 作用 |
| --- | --- | --- |
| `/api/health` | GET | 后端存活探针 |
| `/api/system/status` | GET | PowerContext 与 AI 服务状态（密钥只报"是否配置"） |
| `/api/dashboard` | GET | 学习概览聚合 |
| `/api/profile` | GET / PUT | 读写学习档案 |
| `/api/records` | GET / POST | 读写学习记录 |
| `/api/weaknesses` | GET | 薄弱知识点与证据 |
| `/api/review-plan` | GET | 今日复习计划 |
| `/api/tutor` | POST | AI 问答 + 记忆写回 |
| `/api/memory` | GET | 记忆中心：逐条列出 PowerContext 中的真实条目 |

### 2.2 领域层 `app/services/learning_service.py`

**这是整个产品的核心，也是唯一持有领域规则的地方。**

| 职责 | 实现要点 |
| --- | --- |
| 掌握度计算 | 练习正确率（带先验平滑与时间衰减）与自评加权融合 |
| 薄弱判定 | 需要**负面证据**；单次答错最多判为"需要关注" |
| 复习计划 | 按严重程度排序，每条附上来自真实记录的判定原因 |
| 记忆写回 | 把一次学习活动拆成档案、知识点状态、薄弱点、练习记录四类记忆 |
| 记忆召回 | 把记忆渲染成给 AI 的上下文；没有记忆时明确告诉模型"没有找到" |

### 2.3 AI 层 `app/services/ai_service.py`

可插拔 Provider，只负责"理解与生成"，**不负责判断学习者掌握到什么程度**：

| Provider | 协议 | 端点 |
| --- | --- | --- |
| `OpenAiCompatibleProvider` | OpenAI | `POST {base}/chat/completions` |
| `AnthropicCompatibleProvider` | Anthropic | `POST {base}/messages` |

未配置模型时 `AiService.is_configured` 为 `False`，调用直接抛
`AiNotConfiguredError`，由 `main.py` 映射为 HTTP 503 并带上明确提示。
**任何路径都不会返回伪造的 AI 结果。**

模型承担的具体任务：

1. `extract_insights` — 把一句自然语言解析成结构化学习信息（课程、知识点、
   掌握状态、练习题数、自评）。要求严格 JSON，解析失败时保留原文作为备注，
   不静默丢信息。
2. `answer` — 基于召回的记忆回答。召回为空时，提示词明确要求模型回答
   "我没有找到相关记忆"而不是编造。
3. `generate_practice` — 针对薄弱知识点生成练习题。

### 2.4 记忆层 `app/powercontext/`

`client.py` 是**唯一**与 PowerContext 通信的地方，集中处理鉴权、超时、重试与
错误翻译：

| 异常 | 触发条件 | 映射 HTTP |
| --- | --- | --- |
| `PowerContextTimeout` | 请求超时 | 504 |
| `PowerContextAuthError` | 401 / 403 | 403 |
| `PowerContextUnavailable` | 连接失败或 5xx | 503 |
| `PowerContextRequestError` | 其它 4xx | 400 |

`repository.py` 在客户端之上提供两个语义：

1. **Scope 生命周期** — `ensure_scope()` 解析或创建学习者的 Scope，并写入持久的
   scope binding。
2. **创建或修订** — `upsert_document()` 按记忆的 `key` 查找既有条目：
   找到就修订（`PUT` + `If-Match`），找不到就创建（`POST /v1/memory/remember`）。

### 2.5 记忆信封 `app/memory_document.py`

一条 Atomic Memory 的 `text` 用一个可解析的信封承载：

```text
key: knowledge_state:BCNF 分解
course: 数据库系统
topic: BCNF 分解
mastery: 0.4300
attempts: 3
correct: 1
wrong: 2
---
在「BCNF 分解」上累计练习 3 次，答对 1 次、答错 2 次，正确率约 33%，综合掌握度约 43%。
```

- `key` 之后的行是机器可读字段，用于幂等的 upsert 与页面渲染；
- `---` 之后是自然语言叙述，供 AI 引用；
- 解析是**容错**的：没有信封的旧内容整段作为叙述保留；
  只有形如 `snake_case` 的行才会被当作字段，含冒号的中文句子不会被误读。

---

## 3. 记忆读写流程

### 3.1 写入：一次学习活动

```text
POST /api/records
   │
   ├─ 1. ensure_scope(user_id)
   │     resolve_scope_binding → find_scope_by_external_reference → create_scope
   │     set_scope_binding（持久化映射，重启后仍可解析）
   │
   ├─ 2. 写 learning_record（新建，追加式事件日志）
   │     POST /v1/memory/remember
   │
   ├─ 3. 重新读取全部 learning_record，重算掌握度投影
   │     POST /v1/memory/entries/list
   │
   ├─ 4. 写 knowledge_state（同一 key 则修订）
   │     POST /v1/memory/remember   （新建）
   │     或 PUT  /v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}  （修订）
   │
   └─ 返回：写操作明细 + 新的掌握度
```

### 3.2 读取：AI 助手的一次问答

```text
POST /api/tutor
   │
   ├─ 1. ensure_scope + 读全部记忆
   ├─ 2. POST /v1/memory/search（按问题关键词检索命中的记忆）
   ├─ 3. build_memory_context()  把记忆渲染成给模型的上下文
   │      （没有记忆时上下文里明确写"没有找到相关记忆"）
   ├─ 4. 调用模型生成回答
   └─ 5. extract_insights() 解析本轮输入
          ├─ 更新 learner_profile（课程、目标、重点关注）
          ├─ 写 knowledge_state（自述的知识点状态）
          ├─ 写 weakness（自述的薄弱点）
          └─ 写 learning_record（自述的练习结果）
```

### 3.3 更新：记忆是修订而不是重复追加

这是本项目与"往数据库里不断 INSERT"的根本区别：

| 场景 | 行为 | PowerContext 操作 |
| --- | --- | --- |
| 第一次练习 | 新建条目 | `POST /v1/memory/remember` → revision 1 |
| 同一知识点再次练习 | **修订同一条目** | `GET` 取 etag → `PUT .../artifacts/atomic-memory/{id}` → revision 2 |
| 新的练习记录 | 追加一条事件 | `POST /v1/memory/remember` → 新 artifact |

`knowledge_state` 是**派生投影**，每次都从权威的 `learning_record` 事件日志重算，
并在字段 `record_ids` 中保留它依据的记录 ID；因此重建投影不会悄悄改写历史。

---

## 4. 跨会话记忆是怎么工作的

1. 学习者 ID（`user_id`）是应用的稳定标识。
2. 首次使用时，后端在 PowerContext 中创建一个 Scope，并把
   `user_id` 写进该 Scope 的 `external_references`
   （`kind = studyflow.learner_id`），同时写入一个 scope binding
   （`integration = studyflow`, `kind = learner`, `external_id = user_id`）。
3. 之后的每一次请求，后端都用 `user_id` 解析出同一个 `scope_id`，
   再从该 Scope 读取记忆。
4. **映射关系存在服务端**，不依赖任何本地数据库或浏览器存储。
   后端进程重启、换一台机器，只要连的是同一个 PowerContext Server，
   就能读回同一个学习者的全部记忆。

浏览器 localStorage 只保存"当前选择的是哪个学习者 ID"这一个界面状态，
不保存任何长期记忆。

---

## 5. 用户隔离方案

隔离由 **PowerContext 的 Scope 边界**保证，共有三道防线：

1. **存储隔离** — 每个学习者一个独立 Scope，记忆条目互不可见。
   读取某学习者记忆时只使用该学习者的 `scope_id`。
2. **服务端鉴权** — PowerContext 以 `ACCESS_MODE=enforced` 运行，
   未携带 `Authorization: Bearer <token>` 的请求一律 401。
   作用域访问还要通过 `scope.read` / `scope.contribute` 检查。
3. **绑定隔离** — scope binding 把外部身份固定到 Scope，
   即使 Scope 标题被改写，映射也不会漂移。

**不是靠前端隐藏数据。** 前端拿到的是"当前学习者 ID 对应的那一个 Scope"的内容，
换一个学习者 ID 请求的是另一个 Scope，服务端返回的就是空数据。

---

## 6. 掌握度与薄弱判定规则

### 6.1 掌握度

```text
practice_mastery = (weighted_correct + 2.0 × 0.5) / (weighted_total + 2.0)
    weighted_correct / weighted_total 按记录时间做 0.9^(n-1-i) 衰减加权
    （越近的练习权重越大）

self_mastery = (self_rating - 1) / 4        # 自评 1–5 归一到 0–1

mastery = 0.7 × practice_mastery + 0.3 × self_mastery
    没有自评时 mastery = practice_mastery
```

先验平滑（`+2.0×0.5`）保证只做过一次练习时不会被 0% 或 100% 绑架。

### 6.2 薄弱判定

```text
没有练习证据                          → 不是薄弱点
无负面证据（没答错过且自评 ≥ 3）且练习 < 2 次 → 不是薄弱点
负面证据 且 练习 ≥ 2 且 mastery < 0.50 → high（急需加强）
负面证据 且 答错 ≥ 3 且 mastery < 0.55 → high
负面证据 且 mastery < 0.62             → medium（需要关注）
负面证据 且 mastery < 0.78             → low（轻度薄弱）
```

关键约束：**单次答错最多判为 `medium`**，`high` 需要多次证据。
这条约束由 `tests/test_unit_domain.py::test_single_wrong_answer_is_not_a_high_severity_weakness`
和 `::test_single_correct_answer_is_not_a_weakness` 保护。

### 6.3 推荐原因

每条复习建议的原因文本**只由存储的记录计算得出**，不由模型生成：

```text
在「BCNF 分解」上累计练习 3 次、答错 2 次，答题正确率约 33%，
自评掌握程度约 2.7/5，综合掌握度约 43%，最近几次的正确率比之前有提升。
```

只有真正读到了对应记录，才会出现这样的解释；没有记录就不会有解释。
AI 只负责在此基础上补充练习建议，不负责编造依据。

---

## 7. 失败与降级

| 情况 | 行为 |
| --- | --- |
| PowerContext 不可用 | API 返回 503 与明确信息；界面显示错误而不是空数据 |
| PowerContext 超时 | 返回 504；客户端对瞬时失败做指数退避重试 |
| PowerContext 凭据被拒 | 返回 403，提示检查 `STUDYFLOW_POWERCONTEXT_TOKEN` |
| AI 未配置 | 记忆读写、薄弱分析、复习计划**照常工作**；AI 入口明确提示未配置，不返回伪造内容 |
| AI 服务报错 | 返回 502 并展示真实错误；记忆读回仍可用 |
| 记忆召回失败 | 记 warning 日志，助手仍可回答，只是标注"未检索到相关记忆" |

**核心原则**：学习记录、薄弱分析、复习计划这三项完全由真实练习记录驱动，
不依赖 AI 是否可用。AI 缺席时，产品仍然是可用的。
