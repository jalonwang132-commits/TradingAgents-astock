"""当前请求/线程的用户上下文。

Web 里有两类并发：

1. Streamlit 脚本线程（每个浏览器会话一个），渲染 UI、读写历史/团队配置；
2. 分析工作线程（每个运行一个），跑 graph、取数、写报告。

两者都可能同时属于不同用户。**不能用模块级全局变量**记录"当前用户"——否则 A
的分析线程会把 B 的路径写进报告。这里用 ``contextvars.ContextVar``：每个线程
（以及 asyncio 任务）各有一份，互不干扰。

UI 层每次 rerun 在认证门里调用 :func:`activate`；分析线程在启动时调用同一函数。
模块级函数 :func:`current_paths` 在未登录（CLI / 测试 / 旧单用户模式）时返回
``None``，调用方据此回落到旧路径。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from web.user_paths import UserPaths

_CURRENT: ContextVar[UserPaths | None] = ContextVar("tradingagents_user_paths", default=None)


def current_paths() -> UserPaths | None:
    """返回当前线程绑定的用户目录；未绑定返回 None。"""
    return _CURRENT.get()


def activate(paths: UserPaths, *, run_config: Mapping[str, Any] | None = None) -> UserPaths:
    """把当前线程绑定到某个用户，并同步运行期配置。

    同步 ``set_config`` 很重要：缺失数据、行情缓存、报告保存都通过
    ``tradingagents.dataflows.config.get_config()`` 取路径。不设置的话，UI 线程
    里触发的「重试缺失数据」会写回旧单用户目录。
    """
    _CURRENT.set(paths)

    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.missing_data import set_store_root

    config: dict[str, Any] = {
        "results_dir": str(paths.logs),
        "data_cache_dir": str(paths.cache),
        "memory_log_path": str(paths.memory_log),
        # 供 missing_data 等模块定位 per-user 存储。
        "user_home": str(paths.root),
    }
    if run_config:
        config.update(dict(run_config))
    set_config(config)
    set_store_root(paths.root)
    return paths


def deactivate() -> None:
    """退出登录 / 测试清理时解除绑定，避免残留到下一个账户。"""
    _CURRENT.set(None)

    from tradingagents.dataflows.config import reset_config
    from tradingagents.dataflows.missing_data import set_store_root

    reset_config()
    set_store_root(None)
