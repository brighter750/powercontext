"""OceanBase 服务端资料库。

通过 MySQL 协议连接 OceanBase（租户）实例，逻辑与 seekdb 完全一致。
连接串格式::

    mysql://user:password@host:2881/database?charset=utf8mb4
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import aiomysql

from sddctx.store.mysql_like import MySqlMaterialStore


class OceanBaseStore(MySqlMaterialStore):
    """OceanBase 服务端上的资料库。"""

    def __init__(self, *, dsn: str, table_prefix: str = "sdd", echo: bool = False) -> None:
        super().__init__(table_prefix=table_prefix, echo=echo)
        self.dsn = dsn

    def _parse_dsn(self) -> dict[str, Any]:
        parsed = urlparse(self.dsn)
        if parsed.scheme not in {"mysql", "oceanbase"}:
            raise ValueError(f"unsupported OceanBase DSN scheme: {parsed.scheme!r}")
        options = parse_qs(parsed.query)
        return {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 2881,
            "user": unquote(parsed.username or "root"),
            "password": unquote(parsed.password or ""),
            "database": (parsed.path or "/").lstrip("/") or "test",
            "charset": (options.get("charset") or ["utf8mb4"])[0],
        }

    async def _connect(self) -> aiomysql.Connection:
        options = self._parse_dsn()
        return await aiomysql.connect(
            host=options["host"],
            port=options["port"],
            user=options["user"],
            password=options["password"],
            db=options["database"],
            charset=options["charset"],
            autocommit=True,
        )

    async def __aenter__(self) -> OceanBaseStore:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()


__all__ = ["OceanBaseStore"]
