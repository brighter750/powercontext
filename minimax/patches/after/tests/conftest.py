"""pytest 共享夹具。

让测试既可以在已安装 ``order_pricing`` 的环境中运行，也可以在未安装的
干净仓库中直接运行（通过 ``src`` 目录导入）。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from order_pricing.models import Order, OrderLine  # noqa: E402


def make_order(
    order_id: str = "ORDER-1",
    *,
    unit_price: str = "100",
    quantity: int = 1,
    member_tier: str = "none",
    region: str = "domestic",
) -> Order:
    """构造一个只含单行的订单，便于精确断言金额。"""

    return Order(
        order_id=order_id,
        lines=(OrderLine(sku="SKU-1", unit_price=Decimal(unit_price), quantity=quantity),),
        member_tier=member_tier,
        region=region,
    )


@pytest.fixture
def order_factory():
    return make_order
