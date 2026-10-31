"""金额舍入回归测试 —— SPEC-PRICING-104 / SPEC-INVOICE-301。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from order_pricing.money import quantize


def test_quantize_keeps_two_decimals() -> None:
    """SPEC-PRICING-104：金额一律保留两位小数。"""

    assert quantize(Decimal("12.5")) == Decimal("12.50")


def test_quantize_rounds_half_up() -> None:
    """SPEC-PRICING-104：采用 ROUND_HALF_UP 四舍五入。"""

    assert quantize(Decimal("1.005")) == Decimal("1.01")
    assert quantize(Decimal("1.004")) == Decimal("1.00")


def test_quantize_rejects_non_decimal() -> None:
    """SPEC-COMPAT-403：非 Decimal 输入必须被拒绝。"""

    with pytest.raises(TypeError):
        quantize(1.005)  # type: ignore[arg-type]
