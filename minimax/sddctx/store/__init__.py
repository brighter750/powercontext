"""资料库工厂。"""

from __future__ import annotations

from pathlib import Path

from sddctx.config import SddConfig
from sddctx.store.base import ImportStats, MaterialStore, SearchHit
from sddctx.store.mysql_like import MySqlMaterialStore
from sddctx.store.oceanbase_store import OceanBaseStore
from sddctx.store.seekdb_store import SeekDBStore


def build_store(config: SddConfig) -> MaterialStore:
    """按配置构造资料库实例。"""

    if config.db_backend == "oceanbase":
        if not config.oceanbase_dsn:
            raise RuntimeError(
                "SDDCTX_DB_BACKEND=oceanbase 时必须设置 SDDCTX_OCEANBASE_DSN，"
                "例如 mysql://root:pass@127.0.0.1:2881/test"
            )
        return OceanBaseStore(dsn=config.oceanbase_dsn, table_prefix=config.oceanbase_table_prefix)
    return SeekDBStore(path=config.seekdb_path, table_prefix=config.oceanbase_table_prefix)


__all__ = [
    "ImportStats",
    "MaterialStore",
    "MySqlMaterialStore",
    "OceanBaseStore",
    "SearchHit",
    "SeekDBStore",
    "build_store",
]
