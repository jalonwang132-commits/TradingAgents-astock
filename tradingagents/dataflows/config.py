"""运行期配置（线程/任务级，而非进程级）。

历史背景：这里原本是一个模块级 ``_config`` 字典，``set_config()`` 全局 ``update``。
单用户 CLI 没问题，但 Web 多用户下两个分析线程同时跑时，后启动的那次会覆盖前一次
的 ``strategy_packs`` / ``output_language`` / ``market_lookback_days`` / 缓存目录——
A 的战法会进 B 的提示词。这是**跨用户串数据**，不是性能问题。

改用 ``contextvars.ContextVar``：每个线程 / asyncio 任务各自一份配置，互不覆盖。
同步 LangGraph 在调用线程里执行节点，所以 ``TradingAgentsGraph.__init__`` 里
``set_config`` 之后，后续 agent / 工具 / 提示词构建都能读到同一份。
"""

from typing import Dict, Optional
from contextvars import ContextVar

import tradingagents.default_config as default_config

# 线程/任务级配置；None = 尚未初始化，回落到 DEFAULT_CONFIG。
_config: ContextVar[Optional[Dict]] = ContextVar("tradingagents_dataflow_config", default=None)


def initialize_config():
    """Initialize the configuration with default values for this context."""
    if _config.get() is None:
        _config.set(default_config.DEFAULT_CONFIG.copy())


def set_config(config: Dict):
    """Update the configuration **for the current thread/task** with custom values.

    合并语义与旧实现一致：只覆盖传入的键，其余保留。不会原地修改其它线程的字典。
    """
    current = _config.get()
    if current is None:
        current = default_config.DEFAULT_CONFIG.copy()
    merged = dict(current)
    merged.update(config)
    _config.set(merged)


def get_config() -> Dict:
    """Get the current configuration (a shallow copy)."""
    current = _config.get()
    if current is None:
        current = default_config.DEFAULT_CONFIG.copy()
        _config.set(current)
    return current.copy()


def reset_config() -> None:
    """Drop the context-local override (used on logout / test cleanup)."""
    _config.set(None)


# Initialize with default config for the importing context.
initialize_config()
