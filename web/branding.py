"""品牌信息的唯一来源。

界面、报告文件名、CLI 标题都从这里取。要换成别的名字，只改这一个文件即可，
不用再去组件里逐个搜字符串。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# ── 展示名 ────────────────────────────────────────────────────────────────────
APP_NAME = "Jalon A股投研工作台"
APP_NAME_EN = "Jalon A-Share Research Desk"
APP_MARK_LEFT = "Jalon"
APP_MARK_RIGHT = "A股"
APP_TAGLINE = "多 Agent 投研工作台"
APP_DESCRIPTION = "7 位 AI 分析师 → 质量门控 → 多空辩论 → 风控评估 → 组合决策"

# ── 作者与仓库 ────────────────────────────────────────────────────────────────
AUTHOR = "JalonWang"
AUTHOR_URL = "https://github.com/jalonwang132-commits"
REPO_URL = "https://github.com/jalonwang132-commits/TradingAgents-astock"
REPO_LABEL = "jalonwang132-commits/TradingAgents-astock"

# 下载文件的前缀（避免中文文件名在某些系统上乱码）
FILE_PREFIX = "JalonAstock"


def _detect_version() -> str:
    """优先读安装后的包版本；源码目录直接跑时回落到当前开发版本。"""
    for dist in ("jalon-astock", "tradingagents-astock"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0.7.0"


VERSION = _detect_version()
