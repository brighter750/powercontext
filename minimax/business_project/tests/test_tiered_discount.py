"""阶梯满减目标功能测试 —— SPEC-PRICING-102 / SPEC-PRICING-103。

本文件是本次开发任务新增的目标功能测试。
在阶梯满减规则实现之前，本文件中的阈值档位用例会因“目标行为未实现”而失败。
"""

from __future__ import annotations

from decimal import Decimal

from order_pricing.discounts import apply_discounts, tiered_discount_rate
from order_pricing.models import Order, OrderLine


def _order(unit_price: str, *, member_tier: str = "none", quantity: int = 1) -> Order:
    return Order(
        order_id="T-1",
        lines=(OrderLine("SKU-1", Decimal(unit_price), quantity),),
        member_tier=member_tier,
    )


# --- SPEC-PRICING-102：门槛档位 ------------------------------------------------


def test_below_threshold_has_no_tier_discount() -> None:
    """SPEC-PRICING-102：低于 1000 门槛不打折。"""

    assert tiered_discount_rate(Decimal("999.99")) == Decimal("0")


def test_at_1000_threshold_applies_five_percent() -> None:
    """SPEC-PRICING-102：门槛是闭区间，恰好 1000 适用 0.05。"""

    assert tiered_discount_rate(Decimal("1000")) == Decimal("0.05")


def test_between_1000_and_5000_applies_five_percent() -> None:
    """SPEC-PRICING-102：1000 与 5000 之间适用 0.05。"""

    assert tiered_discount_rate(Decimal("4999.99")) == Decimal("0.05")


def test_at_5000_threshold_applies_ten_percent() -> None:
    """SPEC-PRICING-102：恰好 5000 适用 0.10。"""

    assert tiered_discount_rate(Decimal("5000")) == Decimal("0.10")


def test_between_5000_and_10000_applies_ten_percent() -> None:
    """SPEC-PRICING-102：5000 与 10000 之间适用 0.10。"""

    assert tiered_discount_rate(Decimal("9999.99")) == Decimal("0.10")


def test_at_10000_threshold_applies_fifteen_percent() -> None:
    """SPEC-PRICING-102：恰好 10000 适用 0.15。"""

    assert tiered_discount_rate(Decimal("10000")) == Decimal("0.15")


def test_only_the_highest_tier_applies() -> None:
    """SPEC-PRICING-102：超过 10000 时只取 0.15 一档，不与 0.05 / 0.10 叠加。"""

    assert tiered_discount_rate(Decimal("20000")) == Decimal("0.15")


# --- SPEC-PRICING-103：叠加顺序 ------------------------------------------------


def test_tier_threshold_uses_member_discounted_subtotal() -> None:
    """SPEC-PRICING-103：门槛按会员折扣之后的小计判断。

    小计 1000、gold 会员（5%）后为 950，低于 1000 门槛，因此不应触发阶梯满减。
    如果实现错误地使用折扣前的小计，这里会得到 0.05。
    """

    result = apply_discounts(_order("1000", member_tier="gold"))
    assert result.member_rate == Decimal("0.05")
    assert result.tier_rate == Decimal("0")
    assert result.discounted_subtotal == Decimal("950.00")


def test_tier_applies_when_member_discounted_subtotal_reaches_threshold() -> None:
    """SPEC-PRICING-103：小计 1050、gold 会员后为 997.5，仍不触发；小计 1053、none 会员为 1053，触发 0.05。"""

    below = apply_discounts(_order("1050", member_tier="gold"))
    assert below.tier_rate == Decimal("0")
    assert below.discounted_subtotal == Decimal("997.50")

    above = apply_discounts(_order("1053"))
    assert above.tier_rate == Decimal("0.05")
    assert above.discounted_subtotal == Decimal("1000.35")


def test_apply_discounts_reports_tier_rate_and_final_amount() -> None:
    """SPEC-PRICING-105 / SPEC-PRICING-103：结果必须暴露阶梯折扣率与最终金额。"""

    result = apply_discounts(_order("10000"))
    assert result.base_subtotal == Decimal("10000")
    assert result.member_rate == Decimal("0")
    assert result.tier_rate == Decimal("0.15")
    assert result.discounted_subtotal == Decimal("8500.00")


def test_apply_discounts_applies_member_then_tier() -> None:
    """SPEC-PRICING-103：小计 12000、gold 会员：先打 5% 得到 11400，再按 11400 命中 0.15 档。"""

    result = apply_discounts(_order("12000", member_tier="gold"))
    assert result.member_rate == Decimal("0.05")
    assert result.tier_rate == Decimal("0.15")
    assert result.discounted_subtotal == Decimal("9690.00")
