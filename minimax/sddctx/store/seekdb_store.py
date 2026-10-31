"""嵌入式 seekdb 资料库。

依赖 ``pylibseekdb``（官方 seekdb Python 绑定）。它会在本机启动一个 seekdb 实例，
并通过 MySQL 协议暴露端口，因此本实现复用 :class:`MySqlMaterialStore` 的全部逻辑。

用法::

    async with SeekDBStore(path=Path("./seekdb-data")) as store:
        await store.initialize()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiomysql

from sddctx.store.mysql_like import MySqlMaterialStore


class SeekDBStore(MySqlMaterialStore):
    """本地嵌入式 seekdb 实例上的资料库。"""

    def __init__(
        self,
        *,
        path: Path,
        table_prefix: str = "sdd",
        database: str = "test",
        echo: bool = False,
    ) -> None:
        super().__init__(table_prefix=table_prefix, echo=echo)
        self.path = path
        self.database = database
        self._instance: Any | None = None

    async def _connect(self) -> aiomysql.Connection:
        try:
            import pylibseekdb
        except ModuleNotFoundError as error:  # pragma: no cover - 环境问题
            raise RuntimeError(
                "缺少 pylibseekdb：嵌入式 seekdb 需要 `pip install pylibseekdb==1.4.0.post2`"
                "（Windows / Linux / macOS 均有对应 wheel）。"
            ) from error

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._instance = await pylibseekdb.aopen(str(self.path))
        options = dict(self._instance.connection_options())
        options.pop("user", None)
        password = options.pop("password", None)
        host = str(options.pop("host", "127.0.0.1"))
        port = int(options.pop("port", 2881))
        options.pop("unix_socket", None)

        connection = await aiomysql.connect(
            host=host,
            port=port,
            user="root",
            password=password,
            db=self.database,
            charset="utf8mb4",
            autocommit=True,
        )
        return connection

    async def close(self) -> None:
        await super().close()
        if self._instance is not None:
            self._instance.close()
            self._instance = None

    async def __aenter__(self) -> SeekDBStore:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()


__all__ = ["SeekDBStore"]
