# 验证样例：实现阶梯满减（SPEC-PRICING-102）

## 1. 任务描述

> 实现订单计价中的**阶梯满减**规则：按**会员折扣之后的小计**判断门槛档位，
> 命中 `1000 / 5000 / 10000` 三档之一，**只取最高一档、不叠加**；
> 会员折扣必须先于阶梯满减计算。

任务 ID：`TASK-001`

## 2. 本次任务适用的规格条目

| 规格编号 | 标题 | 来源 | 作用 |
| --- | --- | --- | --- |
| **SPEC-PRICING-102** | 阶梯满减（本次任务要实现的目标行为） | `business_project/specs/SPEC-001-pricing.md:13` | **要实现** |
| SPEC-PRICING-103 | 叠加顺序 | `business_project/specs/SPEC-001-pricing.md:29` | **要遵守** |
| SPEC-PRICING-105 | 结果可读性 | `business_project/specs/SPEC-001-pricing.md:43` | **要遵守** |
| SPEC-PRICING-101 | 会员等级折扣 | `business_project/specs/SPEC-001-pricing.md:6` | 必须保持 |
| SPEC-COMPAT-401 | 既有公开函数签名 | `business_project/specs/SPEC-003-compatibility.md:7` | 必须保持 |
| SPEC-COMPAT-403 | 既有边界行为 | `business_project/specs/SPEC-003-compatibility.md:28` | 必须保持 |
| SPEC-SHIPPING-201 | 免运费门槛 | `business_project/specs/SPEC-002-shipping-invoice.md:5` | 必须保持 |
| SPEC-INVOICE-302 | 发票总额构成 | `business_project/specs/SPEC-002-shipping-invoice.md:22` | 必须保持 |

> 上表的行号来自 `python -m sddctx context` 的实际输出（见 `evidence/db/`），
> 规格更新后行号会随之变化，模块会输出新的 `source_path:source_line` 与 `revision`。

## 3. 需要修改的代码

| 文件 | 符号 | 说明 |
| --- | --- | --- |
| `src/order_pricing/discounts.py` | `tiered_discount_rate(discounted_subtotal)` | **本次要实现**的阶梯档位判定 |
| `src/order_pricing/discounts.py` | `apply_discounts(order)` | 已按 SPEC-PRICING-103 串好顺序，无需改动 |

模块通过“规格正文点名的标识符 → 代码符号”这条链路定位到上面两个符号，
不依赖任务描述里是否出现英文函数名。

## 4. 验证用测试

| 类别 | 文件 | 说明 |
| --- | --- | --- |
| **目标功能测试（新增）** | `tests/test_tiered_discount.py` | 11 个用例覆盖门槛档位、闭区间边界、只取最高档、门槛按会员折后小计判断、叠加顺序 |
| 回归测试 | `tests/test_member_discount.py` | SPEC-PRICING-101 / SPEC-COMPAT-403 |
| 回归测试 | `tests/test_shipping.py` | SPEC-SHIPPING-201 / 202 |
| 回归测试 | `tests/test_invoice.py` | SPEC-INVOICE-301 / 302 |
| 回归测试 | `tests/test_money.py` | SPEC-PRICING-104 |

**修改前后使用同一组测试文件**：未删除断言、未跳过用例、未修改预期结果。

## 5. 初始数据

| 数据 | 位置 | 初始状态 |
| --- | --- | --- |
| 规格文档 | `minimax/business_project/specs/*.md` | 3 个文件 / 13 条规格条目 |
| 业务实现 | `minimax/business_project/src/order_pricing/` | 阶梯满减**未实现**（`tiered_discount_rate` 返回中性折扣率 `0`） |
| 测试 | `minimax/business_project/tests/` | 目标功能测试**已存在且失败** |
| 资料库 | `minimax/.sddctx-run/seekdb`（seekdb）或 OceanBase | 空表起步，由 `init` + `import` 建立 |
| PowerContext 状态 | PowerContext SQLite | 空，由 `state save` 写入 |

### 初始数据产生的“修改前失败”

`discounts.tiered_discount_rate` 的初始实现：

```python
def tiered_discount_rate(discounted_subtotal: Decimal) -> Decimal:
    """..."""
    return NEUTRAL_DISCOUNT_RATE   # 0 —— 阶梯规则尚未实现
```

因此 `tests/test_tiered_discount.py` 中的阈值档位用例会以
`AssertionError: assert Decimal('0') == Decimal('0.05')` 形式失败——
是**目标行为缺失导致的功能失败**，不是依赖缺失或环境错误。

## 6. 操作步骤

```bash
# 0) 准备
cp minimax/.env.example minimax/.env         # 填入 MINIMAX API Key
powercontext server run                        # 终端 A：启动 PowerContext Server

# 1) 复位并初始化（从空状态开始）
cd minimax
python demo/run_demo.py --reset

# 2) 或手工分步执行
python -m sddctx init
python -m sddctx import
python -m sddctx search --query "阶梯满减 门槛 tiered_discount_rate" --limit 12
python -m sddctx context --task "实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位" \
                         --task-id TASK-001 --out context.md
python -m sddctx state save --task-id TASK-001 --file state-round1.json
python -m sddctx patch --task "实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位" \
                        --task-id TASK-001
python -m sddctx state show --task-id TASK-001     # ← 模块重启后读取状态

# 3) 运行测试
cd business_project
python -m pytest tests -q
```

## 7. 预期结果

| 步骤 | 预期 |
| --- | --- |
| `init` | `{"initialized": true, ...}`，各表计数为 0 |
| `import` | `specs 13 / code_symbols 21 / tests 29`，写入倒排索引约 1782 条 |
| `search` | 返回规格条目（如 `SPEC-PRICING-102`）与测试用例命中，附词频得分 |
| `context`（首次） | 命中规格带 `source_path:source_line` 与 `revision`；命中代码符号带 `file:line` 与签名；命中测试带覆盖的规格编号；任务进展为“尚未保存任务状态” |
| `state save` | 返回 `scope_id` 与 `task_id`；PowerContext 中出现 Work Contract、Source 与 Atomic Memory 记录 |
| `patch` | MiniMax 返回 unified diff；补丁被应用；`changed_files` 指向 `src/order_pricing/discounts.py` |
| `state show`（重启后） | 返回与保存时一致的任务 ID、规格、代码、测试、已完成、待办与验证结果 |
| `pytest`（修改前） | **9 failed, 20 passed** —— 失败全部集中在 `tests/test_tiered_discount.py`，且为 `AssertionError` |
| `pytest`（修改后） | **29 passed** —— 目标功能测试与回归测试全部通过 |

## 8. 重置方法

```bash
# 方式一：删除整个运行目录（资料库 + 证据 + 日志）
rm -rf minimax/.sddctx-run            # Windows: Remove-Item -Recurse -Force minimax\.sddctx-run

# 方式二：只重置资料库
rm -rf minimax/.sddctx-run/seekdb

# 方式三：脚本内置
cd minimax && python demo/run_demo.py --reset
```

PowerContext 侧的状态通过 **Scope** 隔离：换一个 `SDDCTX_POWERCONTEXT_SCOPE`
（或删除该 Scope）即可从空状态重新开始。

## 9. 验证清单（对照六项必做条件）

| 验收项 | 本样例中的证据 |
| --- | --- |
| 能够运行 | `RUNBOOK.md` §4–§7；`evidence/logs/` |
| MiniMax 实际参与 | `evidence/minimax/`（提示词、原始响应、模型名、用量、耗时） |
| 上下文正确 | `evidence/db/context-output.md`（含来源路径与行号） |
| 数据库实际参与 | `evidence/db/`（引擎版本、建表 DDL、真实查询与结果） |
| 状态能够复用 | `evidence/state/`（写入回执 + 重启后读取结果） |
| 代码修改有效 | `evidence/logs/before-tests.log` 与 `after-tests.log`（同一组测试） |
