"""生成修改前后的代码快照与 unified diff。

用法::

    python minimax/scripts/make_patches.py
"""

from __future__ import annotations

import argparse
import difflib
import shutil
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
BUSINESS = MINIMAX_DIR / "business_project"
PATCHES = MINIMAX_DIR / "patches"

TRACKED = (
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
    "tests/test_tiered_discount.py",
)


def snapshot(source: Path, target: Path) -> None:
    for relative in TRACKED:
        source_file = source / relative
        target_file = target / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)


def build_diff(before: Path, after: Path) -> str:
    chunks: list[str] = []
    for relative in TRACKED:
        old = (before / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        new = (after / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                n=3,
            )
        )
        if diff:
            chunks.append("".join(diff))
    return "".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成修改前后快照与 diff")
    parser.add_argument(
        "--before", default=str(PATCHES / "before"), help="修改前快照目录"
    )
    parser.add_argument(
        "--after", default=str(PATCHES / "after"), help="修改后快照目录（会被覆盖）"
    )
    parser.add_argument(
        "--diff", default=str(PATCHES / "business-project-changes.diff"), help="diff 输出路径"
    )
    args = parser.parse_args()

    before = Path(args.before)
    after = Path(args.after)
    if after.exists():
        shutil.rmtree(after)
    snapshot(BUSINESS, after)
    print(f"修改后快照 -> {after}")

    diff = build_diff(before, after)
    diff_path = Path(args.diff)
    diff_path.write_text(diff, encoding="utf-8")
    print(f"diff -> {diff_path}（{len(diff.splitlines())} 行）")

    changed = [
        relative
        for relative in TRACKED
        if (before / relative).read_text(encoding="utf-8")
        != (after / relative).read_text(encoding="utf-8")
    ]
    print("发生变化的文件：")
    for relative in changed:
        print(f"  - {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
