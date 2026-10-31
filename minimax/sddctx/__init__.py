"""SDD 上下文管理模块。

面向软件持续演进的工程上下文模块：根据开发任务定位适用的规格、相关代码与测试，
组装成可直接交给开发助手的上下文，并借助 PowerContext 保存与复用任务状态。

模块组成：

* :mod:`sddctx.importer` —— 导入规格、代码与测试资料。
* :mod:`sddctx.store` —— 资料库（seekdb / OceanBase），检索结果直接用于上下文组装。
* :mod:`sddctx.retrieval` —— 基于倒排索引的任务检索。
* :mod:`sddctx.context_builder` —— 开发上下文组装。
* :mod:`sddctx.state` —— 任务状态保存与复用（复用 PowerContext）。
* :mod:`sddctx.llm` —— MiniMax 接入与代码修改生成。
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
