# 验证样例与实际执行结果

本文记录赛题要求的五个验收样例（A–E）的**实际执行过程**，
以及自动化测试套件的真实运行结果。

所有结果均来自真实运行：PowerContext Server 运行在
`http://127.0.0.1:8765`（`ACCESS_MODE=enforced`，带 Bearer 令牌），
StudyFlow 后端运行在 `http://127.0.0.1:8080`。
原始请求/响应保存在 `qoder/evidence/scenarios/*.json`，
PowerContext Server 自身的操作记录保存在
`qoder/evidence/powercontext-server-operations.json`。

复现命令：

```bash
# 1) 启动 PowerContext
powershell -NoProfile -File qoder/scripts/start_powercontext.ps1 -Token "<token>" -OpenDashboard

# 2) 启动后端
powershell -NoProfile -File qoder/scripts/run_backend.ps1

# 3) 执行五个样例
python qoder/scripts/verify_scenarios.py
```

---

## 0. 执行环境

| 项 | 值 |
| --- | --- |
| PowerContext Server | `http://127.0.0.1:8765`，`access_mode=enforced`，`authentication_provider=ready` |
| HTTP API 契约 | 1.4 |
| StudyFlow 后端 | `http://127.0.0.1:8080` |
| 数据库 | SQLite（`qoder/.artifacts/powercontext-studyflow.db`） |
| AI 模型服务 | **未配置**（见下方说明） |
| 执行时间 | 2026-11-01 |

### 关于 AI 模型服务未配置

当前执行环境中**没有任何可用的模型服务凭据**。按赛题要求
「如果模型服务没有配置，应给出清晰提示，不得返回伪造的 AI 结果」，
StudyFlow 的行为是：

* `GET /api/system/status` 返回 `ai_configured: false`；
* 界面显示「AI 模型未配置」；
* `POST /api/tutor` 返回明确提示，**不生成任何回答内容**；
* 复习计划页的练习题区域显示「还没有生成练习题」，**不用假内容填充**；
* **学习记录、薄弱知识点分析、复习计划、记忆中心全部照常工作**——
  这四项由真实练习记录驱动，不依赖模型。

因此，样例 A / B / C 中**依赖自然语言理解**的步骤被如实标记为
「需要配置 AI 模型后复测」，并附带实际收到的响应。
配置模型后重跑同一条命令即可得到完整证据，步骤见
[`DEPLOYMENT.md`](DEPLOYMENT.md#5-配置-ai-模型服务)。

样例 D / E 不依赖模型，已完整通过。

---

## 1. 测试 A：写入学习记忆

| 项 | 内容 |
| --- | --- |
| **测试目的** | 验证学习者口述的学习情况能被写入 PowerContext，并可读回 |
| **初始状态** | 学习者 `student_demo_001` 已在 PowerContext 中拥有 Scope `scp_64abdjagdwpdaavfny60wy29hr`；记忆条目 12 条 |
| **测试输入** | 「我正在学习数据库规范化。函数依赖和候选码基本理解，但 BCNF 分解经常出错。请记住这个情况，以后复习时重点帮我练习 BCNF。」 |
| **实际操作** | `POST /api/tutor`，`save_to_memory=true`；随后 `GET /api/memory` 核对写入结果 |
| **实际输出** | 见 `evidence/scenarios/A_write_memory.json` |
| **PowerContext 调用记录** | `resolve_scope_binding`、`set_scope_binding`、`list_memory_entries`、`search_memory`（Server 日志中带 `request_id`） |
| **结果** | **需要配置 AI 模型后复测** |

**未通过原因**：当前环境未配置模型服务，自然语言理解步骤无法执行。
应用按设计返回了明确的"未配置"提示，没有返回伪造结果。

**配置模型后应观察到的现象**（与同一条命令的其它样例一致）：
应用识别出课程「数据库系统 / 数据库规范化」、把「函数依赖」「候选码」识别为
相对熟悉、把「BCNF 分解」识别为需要加强的知识点，并调用
`POST /v1/memory/remember` 写入 `learner_profile` / `knowledge_state` /
`weakness` 三类记忆；`GET /api/memory` 的条目数增加且新条目带真实 `artifact_id`。

**等价的、不依赖模型的可复现验证**（当前已通过）：

```bash
curl -X POST "http://127.0.0.1:8080/api/records?user_id=student_demo_001" \
  -H "Content-Type: application/json" \
  -d '{"course":"数据库系统","topic":"BCNF 分解","is_correct":false,"self_rating":2,"notes":"BCNF 分解容易出错"}'
```

实测返回 `HTTP 200`，`writes[0].action = "created"`，
`artifact_id = art_...`，并在 PowerContext 中生成对应的
`learning_record` 与 `knowledge_state` 条目。
自动化测试 `test_write_then_read_from_the_real_server` 覆盖了同一条路径。

---

## 2. 测试 B：新会话读取记忆

| 项 | 内容 |
| --- | --- |
| **测试目的** | 验证换一个全新会话后，同一学习者无需重新介绍即可被读回记忆 |
| **初始状态** | 已完成测试 A（或等价的记录写入），PowerContext 中已有该学习者的记忆 |
| **测试输入** | 「我今天应该复习什么？」 |
| **实际操作** | 新建 HTTP 会话 → `POST /api/tutor` → 记录 `memory_context` 与 `memory_hits` |
| **实际输出** | 见 `evidence/scenarios/B_cross_session_read.json` |
| **PowerContext 调用记录** | `resolve_scope_binding`、`list_memory_entries`、`search_memory` |
| **结果** | **需要配置 AI 模型后复测**（记忆召回本身已成功） |

**实测细节**：新会话确实**重新从 PowerContext 读取了记忆**，
`memory_context` 中包含「BCNF 分解」等真实历史内容，
证明跨会话读取通道是通的；只是回答生成步骤因未配置模型而无法完成。

**已通过的不依赖模型的等价验证**：

```bash
curl "http://127.0.0.1:8080/api/review-plan?user_id=student_demo_001"
```

实测返回：

```json
{
  "items": [{
    "topic": "BCNF 分解",
    "priority": 1,
    "reason": "在「BCNF 分解」上累计练习 3 次、答错 2 次，答题正确率约 33%，自评掌握程度约 2.7/5，综合掌握度约 43%，最近几次的正确率比之前有提升。",
    "evidence": "基于 3 次练习"
  }],
  "summary": "今天建议优先复习 1 个知识点，其中「BCNF 分解」最需要关注：……"
}
```

推荐原因引用的是 PowerContext 中真实存在的记录，用户**没有重新介绍任何信息**。
自动化测试 `test_memory_survives_a_restart` 与
`test_reads_are_returned_newest_first` 覆盖了相关行为。

---

## 3. 测试 C：更新学习进度

| 项 | 内容 |
| --- | --- |
| **测试目的** | 验证新一次练习会**更新**已有记忆，而不是重复追加旧内容 |
| **初始状态** | 已完成测试 A / B |
| **测试输入** | 「我刚才做了 5 道 BCNF 练习，答对了 4 道，现在比之前熟悉了。」 |
| **实际操作** | 记录更新前后的全部条目（key / artifact_id / revision）→ `POST /api/tutor` → 再记录一次 |
| **实际输出** | 见 `evidence/scenarios/C_update_progress.json` |
| **结果** | **需要配置 AI 模型后复测** |

**未通过原因**：未配置模型，无法从自然语言中解析出"5 道练习、答对 4 道"。

**等价机制已通过真实集成验证**（不依赖模型，用结构化表单提交同样的信息）：

```bash
curl -X POST "http://127.0.0.1:8080/api/records?user_id=student_demo_001" \
  -H "Content-Type: application/json" \
  -d '{"topic":"BCNF 分解","is_correct":true,"self_rating":4}'
```

实测行为：

| 观察点 | 结果 |
| --- | --- |
| `learning_record` 条目数 | 逐条追加（权威事件日志） |
| `knowledge_state` 条目数 | **始终为 1** |
| 该条目的 `artifact_id` | 保持不变 |
| 该条目的 `revision` | 递增（1 → 2 → …） |
| 掌握度 | 随练习结果上升（示例：31% → 43%） |

自动化测试 `test_update_revises_the_same_artifact_on_the_real_server`、
`test_revision_history_is_preserved_on_the_real_server`、
`test_upsert_is_idempotent_for_the_same_key` 覆盖了同一条路径，
断言 PowerContext 中该 key 的条目只有一条、且 `revision` 递增。

---

## 4. 测试 D：用户隔离 —— **通过**

| 项 | 内容 |
| --- | --- |
| **测试目的** | 验证不同学习者 ID 的记忆完全隔离 |
| **初始状态** | 学习者 A（`student_demo_001`）已有 6 条学习记录、12 条记忆条目 |
| **测试输入** | 为学习者 B（`student_demo_992795_b`）发送「我最近正在学习操作系统。」 |
| **实际操作** | `POST /api/tutor`（学习者 B）→ `GET /api/memory`（学习者 B）→ 与学习者 A 的 Scope 对比 |
| **实际输出** | 见 `evidence/scenarios/D_user_isolation.json` |
| **结果** | **通过** |

**实测证据**：

```json
{
  "scope_a": "scp_64abdjagdwpdaavfny60wy29hr",
  "scope_b": "scp_4gckennc58dfgae6jmgsc9a4zx",
  "user_b_entry_count": 0,
  "user_b_memory_context_leaks_user_a": false
}
```

* 两个学习者对应**不同的 PowerContext Scope**；
* 学习者 B 的 Scope 中 `entry_count = 0`，读不到学习者 A 的任何记录；
* 学习者 B 的召回上下文中**不包含**学习者 A 的任何内容；
* 隔离由 PowerContext 的 Scope 边界在**服务端**保证，不是前端隐藏——
  学习者 B 的请求路径与 A 完全相同，只是 `scope_id` 不同。

---

## 5. 测试 E：服务重启后读取 —— **通过**

| 项 | 内容 |
| --- | --- |
| **测试目的** | 验证记忆在进程/服务重启后依然存在 |
| **初始状态** | 学习者 `student_demo_001` 已有 6 条学习记录、12 条记忆条目 |
| **实际操作** | ① `GET /api/memory`（重启前）→ ② 在**全新 Python 进程**中用全新连接读取 → ③ 另外单独重启 PowerContext Server 后再次读取 |
| **实际输出** | 见 `evidence/scenarios/E_restart_persistence.json` |
| **结果** | **通过** |

**实测证据（全新进程读取）**：

```json
{
  "user_id": "student_demo_001",
  "scope_id": "scp_64abdjagdwpdaavfny60wy29hr",
  "record_count": 6,
  "entry_count": 12,
  "records": [{"topic": "第三范式", "notes": "3NF 的判定还需要多练", "artifact_id": "art_...", "revision": 1}]
}
```

重启前后 `entry_count` 一致（12），`scope_id` 一致，记忆内容一致。

**PowerContext Server 真正重启后的验证**：使用
`qoder/scripts/start_powercontext.ps1` 停止并重启 Server（数据库文件保持不变），
再运行

```bash
python qoder/scripts/read_memory_once.py student_demo_001
```

同样读回 `scope_id=scp_64abdjagdwpdaavfny60wy29hr` 与全部 12 条记忆。
自动化测试 `test_memory_survives_a_restart` 用全新客户端实例覆盖了同一语义。

---

## 6. 自动化测试套件

### 6.1 运行命令与真实输出

```bash
cd qoder
pytest tests -q
```

```text
..................................................                       [100%]
50 passed in 8.11s
```

### 6.2 分层说明

| 层 | 文件 | 用例数 | 依赖 |
| --- | --- | --- | --- |
| 单元 | `tests/test_unit_domain.py` | 14 | 无 |
| Mock | `tests/test_mock_service.py` | 16 | 无（内存替身） |
| 真实集成 | `tests/test_integration_powercontext.py` | 11 | **真实 PowerContext Server** |
| API 集成 | `tests/test_api_integration.py` | 9 | **真实 PowerContext Server + 运行中的后端** |

> Mock 层用 `tests/fakes.py` 里的 `FakePowerContextClient` 驱动**真实的**
> Service 与 Repository 代码，验证 Scope 生命周期与"创建或修订"语义。
> **Mock 结果不能替代真实集成测试**，两者在本文档中分开列出。

### 6.3 赛题要求的八项测试对应关系

| 赛题要求 | 实现位置 | 层级 |
| --- | --- | --- |
| 1. 用户档案创建和读取测试 | `test_profile_is_created_and_read_back`、`test_profile_create_and_read` | Mock + API 集成 |
| 2. 学习记录新增测试 | `test_record_creates_authoritative_event_and_projection`、`test_record_create_and_weakness_update` | Mock + API 集成 |
| 3. 薄弱知识点更新测试 | `test_weaknesses_reflect_real_practice`、`test_weaknesses_are_ordered_by_severity_then_mastery` | Mock + 单元 |
| 4. PowerContext 写入测试 | `test_write_then_read_from_the_real_server`、`test_upsert_is_idempotent_for_the_same_key` | 真实集成 |
| 5. PowerContext 读取测试 | `test_reads_are_returned_newest_first`、`test_recall_by_search_on_the_real_server` | Mock + 真实集成 |
| 6. 跨会话记忆测试 | `test_memory_survives_a_restart`、验收样例 B / E | 真实集成 |
| 7. 用户隔离测试 | `test_different_learners_are_isolated`、`test_learners_are_isolated_on_the_real_server`、`test_user_isolation_through_the_api` | Mock + 真实集成 + API 集成 |
| 8. 服务异常处理测试 | `test_powercontext_failure_surfaces_to_the_caller`、`test_powercontext_timeout_is_distinguishable`、`test_powercontext_auth_error_is_distinguishable`、`test_tutor_without_ai_is_honest` | Mock |

### 6.4 真实集成测试清单（全部通过）

```text
tests/test_integration_powercontext.py::test_scope_is_created_and_bound_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_write_then_read_from_the_real_server PASSED
tests/test_integration_powercontext.py::test_update_revises_the_same_artifact_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_revision_history_is_preserved_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_recall_by_search_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_learners_are_isolated_on_the_real_server PASSED
tests/test_integration_powercontext.py::test_memory_survives_a_restart PASSED
tests/test_integration_powercontext.py::test_upsert_is_idempotent_for_the_same_key PASSED
tests/test_integration_powercontext.py::test_search_never_raises_on_odd_queries[] PASSED
tests/test_integration_powercontext.py::test_search_never_raises_on_odd_queries[不存在的主题] PASSED
tests/test_integration_powercontext.py::test_search_never_raises_on_odd_queries[BCNF] PASSED
```

### 6.5 API 集成测试清单（全部通过）

```text
tests/test_api_integration.py::test_health PASSED
tests/test_api_integration.py::test_system_status_reports_both_services PASSED
tests/test_api_integration.py::test_profile_create_and_read PASSED
tests/test_api_integration.py::test_record_create_and_weakness_update PASSED
tests/test_api_integration.py::test_review_plan_and_dashboard PASSED
tests/test_api_integration.py::test_memory_center_lists_real_entries PASSED
tests/test_api_integration.py::test_user_isolation_through_the_api PASSED
tests/test_api_integration.py::test_tutor_without_ai_is_honest PASSED
tests/test_api_integration.py::test_frontend_is_served PASSED
```

### 6.6 真实集成测试在无 Server 时的行为

`tests/conftest.py` 的 `live_client` fixture 会先调用 `GET /health/ready`；
连不上就 `pytest.skip`，**不会伪装成通过**。因此在没有 PowerContext 的机器上
运行 `pytest` 会得到"部分通过 + 部分跳过"，而不是虚假的全绿。

---

## 7. 证据文件清单

| 文件 | 内容 |
| --- | --- |
| `evidence/scenarios/A_write_memory.json` | 样例 A 的完整请求/响应 |
| `evidence/scenarios/B_cross_session_read.json` | 样例 B 的完整请求/响应与召回上下文 |
| `evidence/scenarios/C_update_progress.json` | 样例 C 更新前后的条目对照 |
| `evidence/scenarios/D_user_isolation.json` | 样例 D 的两个 Scope 对比 |
| `evidence/scenarios/E_restart_persistence.json` | 样例 E 的全新进程读取输出 |
| `evidence/powercontext-server-operations.json` | PowerContext Server 侧的操作记录（`request_id` / `status_code` / `duration_ms`） |

重新生成：

```bash
python qoder/scripts/verify_scenarios.py
python qoder/scripts/export_server_log.py
```

---

## 8. 尚未解决的问题

| 问题 | 影响 | 处理方式 |
| --- | --- | --- |
| 当前环境无模型服务凭据 | 样例 A / B / C 的自然语言步骤无法执行 | 按赛题要求如实标记；配置模型后重跑同一条命令补测 |
| 尚未在 Qoder Sites 发布项目网站 | 提交材料中的"项目网站网址"暂缺 | 站点源码已备好在 `qoder/site/`，需用参赛者本人的 Qoder 账号发布 |
| 尚未产生真实用户使用数据 | "使用效果"维度缺少数据 | 属于加分项，需邀请同学实际使用后补充 |
