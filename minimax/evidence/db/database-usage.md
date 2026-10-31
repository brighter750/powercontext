# 资料库使用记录

- 执行时间(UTC)：2026-10-31T15:13:40+00:00
- 后端类型：`seekdb`

## 1. 数据库类型与版本

```sql
SELECT VERSION();
```

```text
5.7.25-OceanBase seekdb-v1.4.0.0
```

## 2. 表结构（DDL）

### `sdd_spec_item`

```sql
CREATE TABLE `sdd_spec_item` (
  `spec_id` varchar(160) NOT NULL,
  `title` varchar(512) NOT NULL,
  `body` text NOT NULL,
  `source_path` varchar(512) NOT NULL,
  `source_line` int(11) NOT NULL,
  `revision` int(11) NOT NULL,
  `content_hash` varchar(64) NOT NULL,
  `tags` varchar(512) NOT NULL,
  `updated_at` varchar(40) NOT NULL,
  PRIMARY KEY (`spec_id`)
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0
```

### `sdd_code_symbol`

```sql
CREATE TABLE `sdd_code_symbol` (
  `item_id` varchar(512) NOT NULL,
  `symbol` varchar(256) NOT NULL,
  `kind` varchar(32) NOT NULL,
  `file_path` varchar(512) NOT NULL,
  `line` int(11) NOT NULL,
  `signature` varchar(1024) NOT NULL,
  `source_path` varchar(512) NOT NULL,
  `revision` int(11) NOT NULL,
  `content_hash` varchar(64) NOT NULL,
  `updated_at` varchar(40) NOT NULL,
  PRIMARY KEY (`item_id`)
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0
```

### `sdd_test_case`

```sql
CREATE TABLE `sdd_test_case` (
  `item_id` varchar(512) NOT NULL,
  `test_name` varchar(256) NOT NULL,
  `file_path` varchar(512) NOT NULL,
  `line` int(11) NOT NULL,
  `source_path` varchar(512) NOT NULL,
  `requirement_ids` varchar(512) NOT NULL,
  `docstring` text NOT NULL,
  `revision` int(11) NOT NULL,
  `content_hash` varchar(64) NOT NULL,
  `updated_at` varchar(40) NOT NULL,
  PRIMARY KEY (`item_id`)
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0
```

### `sdd_token`

```sql
CREATE TABLE `sdd_token` (
  `token` varchar(128) NOT NULL,
  `item_kind` varchar(16) NOT NULL,
  `item_id` varchar(512) NOT NULL,
  `tf` int(11) NOT NULL,
  PRIMARY KEY (`token`, `item_kind`, `item_id`)
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0
```

## 3. 表内数据量

| 表 | 行数 |
| --- | --- |
| `sdd_spec_item` | 13 |
| `sdd_code_symbol` | 21 |
| `sdd_test_case` | 29 |
| `sdd_token` | 1783 |

## 4. 规格条目的修订版本

| 规格编号 | revision | content_hash | updated_at |
| --- | --- | --- | --- |
| `SPEC-COMPAT-401` | 1 | `1efe9639d612ae8b…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-COMPAT-402` | 1 | `9203dc2e753fad09…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-COMPAT-403` | 1 | `37aaf0880263ae5a…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-COMPAT-404` | 1 | `0977a8364b1cf8dc…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-INVOICE-301` | 1 | `2d62dbc492348202…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-INVOICE-302` | 1 | `e0afe441292dd896…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-PRICING-101` | 1 | `d16d9647e5cabb67…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-PRICING-102` | 1 | `7641cb52596d768c…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-PRICING-103` | 1 | `2f2cc5c3b7040483…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-PRICING-104` | 1 | `8f6d6a3f23a86eb9…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-PRICING-105` | 1 | `854c49539e44040e…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-SHIPPING-201` | 1 | `fac678e9e0aef8b6…` | 2026-10-31T15:13:10+00:00 |
| `SPEC-SHIPPING-202` | 1 | `23044a4197308d2d…` | 2026-10-31T15:13:10+00:00 |

## 5. 实际检索查询（上下文即由这次查询的结果组装）

任务描述：实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，只取最高一档，不叠加；会员折扣先于阶梯满减计算。

分词结果（39 个）：`1000`, `10000`, `5000`, `一档`, `不叠`, `不叠加`, `于阶`, `会员`, `会员折扣先于阶梯满减计算`, `先于`, `减折`, `减计`, `取最`, `叠加`, `只取`, `只取最高一档`, `后小`, `员折`, `命中`, `实现`, `实现阶梯满减折扣`, `小计`, `扣先`, `折后`, `折扣`, `按会`, `按会员折后小计命中`, `最高`, `档位`, `梯满`, `槛档`, `满减`, `现阶`, `计命`, `计算`, `门槛`, `门槛档位`, `阶梯`, `高一`

```sql
SELECT item_kind, item_id, SUM(tf) AS score
FROM sdd_token
WHERE token IN ('1000', '10000', '5000', '一档', '不叠', '不叠加', '于阶', '会员', '会员折扣先于阶梯满减计算', '先于', '减折', '减计', '取最', '叠加', '只取', '只取最高一档', '后小', '员折', '命中', '实现', '实现阶梯满减折扣', '小计', '扣先', '折后', '折扣', '按会', '按会员折后小计命中', '最高', '档位', '梯满', '槛档', '满减', '现阶', '计命', '计算', '门槛', '门槛档位', '阶梯', '高一')
GROUP BY item_kind, item_id
ORDER BY score DESC, item_kind ASC, item_id ASC
LIMIT 20;
```

| 类型 | 条目 | 词频得分 |
| --- | --- | --- |
| spec | `SPEC-PRICING-102` | 41 |
| spec | `SPEC-PRICING-103` | 23 |
| test | `tests/test_tiered_discount.py::test_tier_threshold_uses_member_discounted_subtotal` | 20 |
| spec | `SPEC-COMPAT-401` | 14 |
| spec | `SPEC-PRICING-105` | 12 |
| spec | `SPEC-SHIPPING-201` | 12 |
| spec | `SPEC-PRICING-101` | 10 |
| test | `tests/test_member_discount.py::test_apply_discounts_exposes_intermediate_values` | 7 |
| spec | `SPEC-INVOICE-302` | 6 |
| test | `tests/test_tiered_discount.py::test_apply_discounts_applies_member_then_tier` | 6 |
| test | `tests/test_tiered_discount.py::test_between_1000_and_5000_applies_five_percent` | 6 |
| test | `tests/test_tiered_discount.py::test_between_5000_and_10000_applies_ten_percent` | 6 |
| test | `tests/test_tiered_discount.py::test_only_the_highest_tier_applies` | 6 |
| test | `tests/test_tiered_discount.py::test_tier_applies_when_member_discounted_subtotal_reaches_threshold` | 6 |
| test | `tests/test_tiered_discount.py::test_at_1000_threshold_applies_five_percent` | 4 |
| spec | `SPEC-COMPAT-403` | 3 |
| spec | `SPEC-INVOICE-301` | 3 |
| test | `tests/test_invoice.py::test_invoice_applies_member_discount` | 3 |
| test | `tests/test_invoice.py::test_invoice_contains_subtotal_shipping_tax_and_total` | 3 |
| test | `tests/test_member_discount.py::test_no_member_has_no_discount` | 3 |

## 6. 检索结果如何被上下文使用

| 命中类型 | 命中条目 | 在上下文中的位置 |
| --- | --- | --- |
| spec | `SPEC-PRICING-102` | “适用的规格条目” → `specs/SPEC-001-pricing.md:13`（revision=1） |
| spec | `SPEC-PRICING-103` | “适用的规格条目” → `specs/SPEC-001-pricing.md:29`（revision=1） |
| spec | `SPEC-COMPAT-401` | “适用的规格条目” → `specs/SPEC-003-compatibility.md:7`（revision=1） |
| spec | `SPEC-PRICING-105` | “适用的规格条目” → `specs/SPEC-001-pricing.md:43`（revision=1） |
| spec | `SPEC-SHIPPING-201` | “适用的规格条目” → `specs/SPEC-002-shipping-invoice.md:5`（revision=1） |
| spec | `SPEC-PRICING-101` | “适用的规格条目” → `specs/SPEC-001-pricing.md:6`（revision=1） |
| test | `tests/test_tiered_discount.py::test_tier_threshold_uses_member_discounted_subtotal` | “用于验证功能与兼容性的测试” → `tests/test_tiered_discount.py:71` `test_tier_threshold_uses_member_discounted_subtotal` |
| test | `tests/test_member_discount.py::test_apply_discounts_exposes_intermediate_values` | “用于验证功能与兼容性的测试” → `tests/test_member_discount.py:97` `test_apply_discounts_exposes_intermediate_values` |
| test | `tests/test_tiered_discount.py::test_apply_discounts_applies_member_then_tier` | “用于验证功能与兼容性的测试” → `tests/test_tiered_discount.py:106` `test_apply_discounts_applies_member_then_tier` |
| test | `tests/test_tiered_discount.py::test_between_1000_and_5000_applies_five_percent` | “用于验证功能与兼容性的测试” → `tests/test_tiered_discount.py:38` `test_between_1000_and_5000_applies_five_percent` |
| test | `tests/test_tiered_discount.py::test_between_5000_and_10000_applies_ten_percent` | “用于验证功能与兼容性的测试” → `tests/test_tiered_discount.py:50` `test_between_5000_and_10000_applies_ten_percent` |
| test | `tests/test_tiered_discount.py::test_only_the_highest_tier_applies` | “用于验证功能与兼容性的测试” → `tests/test_tiered_discount.py:62` `test_only_the_highest_tier_applies` |

## 7. 规格 → 代码 与 规格 → 测试 的关联查询

```sql
SELECT symbol, file_path, line, kind FROM sdd_code_symbol WHERE symbol IN ('tiered_discount_rate', 'apply_discounts');
```

- `src/order_pricing/discounts.py:68` `tiered_discount_rate` — `def tiered_discount_rate(discounted_subtotal: Decimal) -> Decimal:`
- `src/order_pricing/discounts.py:81` `apply_discounts` — `def apply_discounts(order: Order) -> DiscountResult:`

```sql
SELECT test_name, file_path, line, requirement_ids FROM sdd_test_case WHERE requirement_ids LIKE '%SPEC-PRICING-102%' ORDER BY line;
```

- `tests/test_tiered_discount.py:26` `test_below_threshold_has_no_tier_discount` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:32` `test_at_1000_threshold_applies_five_percent` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:38` `test_between_1000_and_5000_applies_five_percent` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:44` `test_at_5000_threshold_applies_ten_percent` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:50` `test_between_5000_and_10000_applies_ten_percent` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:56` `test_at_10000_threshold_applies_fifteen_percent` ← 覆盖 SPEC-PRICING-102
- `tests/test_tiered_discount.py:62` `test_only_the_highest_tier_applies` ← 覆盖 SPEC-PRICING-102
