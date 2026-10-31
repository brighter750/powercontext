# 规格更新验证记录

- 执行时间(UTC)：2026-10-31T15:14:09+00:00
- 规格文件：`minimax/business_project/specs/SPEC-001-pricing.md`

## 步骤 1：修改前的上下文

- 规格 `SPEC-PRICING-102` 的 revision = **1**

修改前上下文中的阶梯表：

```
| `discounted_subtotal` 门槛 | 满减折扣率 | 折后系数 |
| --- | --- | --- |
| `>= 10000` | `0.15` | `0.85` |
| `>= 5000` | `0.10` | `0.90` |
| `>= 1000` | `0.05` | `0.95` |
| `< 1000` | `0`（不打折） | `1` |
```

## 步骤 2：临时修改规格并重新导入

- 规格改动：`| `>= 1000` | `0.05` | `0.95` |` → `| `>= 1000` | `0.08` | `0.92` |`

重新导入的输出（统计到 `changes.specs_updated`）：

```json
{
  "backend": "seekdb",
  "project_root": "C:\\Users\\mazhe\\powercontext\\minimax\\business_project",
  "imported": {
    "specs": 13,
    "code_symbols": 21,
    "tests": 29,
    "source_files": 15
  },
  "changes": {
    "specs_inserted": 0,
    "specs_updated": 1,
    "specs_unchanged": 12,
    "code_inserted": 0,
    "code_updated": 0,
    "code_unchanged": 21,
    "tests_inserted": 0,
    "tests_updated": 0,
    "tests_unchanged": 29,
    "tokens_written": 1784,
    "source_files": [
      "specs/SPEC-001-pricing.md",
      "specs/SPEC-002-shipping-invoice.md",
      "specs/SPEC-003-compatibility.md",
      "src/order_pricing/__init__.py",
      "src/order_pricing/discounts.py",
      "src/order_pricing/invoice.py",
      "src/order_pricing/models.py",
      "src/order_pricing/money.py",
      "src/order_pricing/shipping.py",
      "tests/conftest.py",
      "tests/test_invoice.py",
      "tests/test_member_discount.py",
      "tests/test_money.py",
      "tests/test_shipping.py",
      "tests/test_tiered_discount.py"
    ]
  }
}
```

- 重新导入后 `SPEC-PRICING-102` 的 revision = **2**

重新导入后的上下文中的阶梯表：

```
| `discounted_subtotal` 门槛 | 满减折扣率 | 折后系数 |
| --- | --- | --- |
| `>= 10000` | `0.15` | `0.85` |
| `>= 5000` | `0.10` | `0.90` |
| `>= 1000` | `0.08` | `0.92` |
| `< 1000` | `0`（不打折） | `1` |
```

## 步骤 3：结论

- 修改前上下文出现 `0.05`，修改后上下文出现 `0.08`，且 `revision` 递增 —— **模块使用的是更新后的规格，而不是固定答案**。

- 完整上下文见 `context-spec-update-before.md` 与 `context-spec-update-after.md`。

## 步骤 4：还原规格（交付物保持与实现、测试一致）

- 重新导入后 revision = **3**

还原后的上下文中的阶梯表：

```
| `discounted_subtotal` 门槛 | 满减折扣率 | 折后系数 |
| --- | --- | --- |
| `>= 10000` | `0.15` | `0.85` |
| `>= 5000` | `0.10` | `0.90` |
| `>= 1000` | `0.05` | `0.95` |
| `< 1000` | `0`（不打折） | `1` |
```
