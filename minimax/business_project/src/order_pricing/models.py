"""订单领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

MEMBER_TIERS = ("none", "silver", "gold")


@dataclass(frozen=True)
class OrderLine:
    """订单行。"""

    sku: str
    unit_price: Decimal
    quantity: int
    category: str = "general"

    def __post_init__(self) -> None:
        if self.unit_price < 0:
            raise ValueError(f"unit_price must not be negative: {self.unit_price}")
        if self.quantity < 0:
            raise ValueError(f"quantity must not be negative: {self.quantity}")


@dataclass(frozen=True)
class Order:
    """订单。"""

    order_id: str
    lines: tuple[OrderLine, ...] = field(default_factory=tuple)
    member_tier: str = "none"
    region: str = "domestic"


__all__ = ["MEMBER_TIERS", "Order", "OrderLine"]
