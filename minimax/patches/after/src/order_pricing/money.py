"""金额工具：统一的两位小数与四舍五入规则。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def quantize(amount: Decimal) -> Decimal:
    """把金额规整到两位小数，使用 ROUND_HALF_UP。

    见 SPEC-PRICING-104 / SPEC-INVOICE-301。
    """

    if not isinstance(amount, Decimal):
        raise TypeError(f"amount must be Decimal, got {type(amount).__name__}")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


__all__ = ["CENTS", "quantize"]
