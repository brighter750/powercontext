"""中英文混合分词器（零外部依赖）。

* ASCII 标识符：整词 + 下划线切分后的子词（``tiered_discount_rate`` →
  ``tiered_discount_rate`` / ``tiered`` / ``discount`` / ``rate``）。
* 规格编号：``SPEC-PRICING-102`` → ``spec-pricing-102`` / ``spec`` / ``pricing`` / ``102``。
* CJK 文本：连续汉字串本身 + 相邻二字组合（bigram），保证中文短语可被检索到。

分词结果同时用于**写入**倒排索引与**查询**倒排索引，两侧口径必须一致。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_ASCII_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASCII_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CJK_RUN = re.compile(r"[㐀-鿿]+")
_SPEC_ID = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "def",
        "class",
        "import",
        "return",
        "self",
        "none",
        "true",
        "false",
        "str",
        "int",
        "bool",
    }
)


def _split_identifier(token: str) -> list[str]:
    parts = [part for part in token.lower().split("_") if part]
    return parts if len(parts) > 1 else []


def _cjk_tokens(run: str) -> list[str]:
    tokens = [run]
    if len(run) > 1:
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def tokenize(text: str) -> list[str]:
    """把文本切成检索词。

    返回的词元全部小写；重复词元保留（倒排索引按出现次数统计词频）。
    """

    if not text:
        return []
    tokens: list[str] = []
    for match in _SPEC_ID.finditer(text):
        spec = match.group(0).lower()
        tokens.append(spec)
        tokens.extend(part for part in spec.split("-") if part)
    for match in _ASCII_WORD.finditer(text):
        word = match.group(0).lower()
        tokens.append(word)
        tokens.extend(_split_identifier(word))
    for match in _ASCII_NUMBER.finditer(text):
        tokens.append(match.group(0))
    for match in _CJK_RUN.finditer(text):
        tokens.extend(_cjk_tokens(match.group(0)))
    return [token for token in tokens if token and token not in _STOPWORDS and len(token) <= 64]


def token_set(text: str) -> set[str]:
    """去重后的词元集合。"""

    return set(tokenize(text))


def join_query(tokens: Iterable[str]) -> str:
    """把词元拼成可安全嵌入 SQL 的字符串。"""

    return " ".join(sorted({token for token in tokens if token}))


__all__ = ["join_query", "token_set", "tokenize"]
