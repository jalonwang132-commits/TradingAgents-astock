"""每用户的数据根目录与文件布局（多用户隔离的唯一事实来源）。

单用户时代所有用户态文件都平铺在 ``~/.tradingagents/`` 下；多用户模式下必须
按账户分目录，否则 A 的分析历史、模型选择、团队配置会被 B 覆盖或读到。

**owner 例外**：第一个账户（owner）继续使用旧的单用户根目录，这样 CLI 和 Web
看到的是同一份历史，升级不需要搬运/复制可能很大的 logs/cache。普通成员严格隔离
在 ``users/<uid>/``。两者共用同一套 ``UserPaths`` 接口，调用方不需要分支。

成员布局（``root`` = ``~/.tradingagents/users/<uid>/``；owner 的 root 就是
``~/.tradingagents/``）::

    root/
      logs/                     分析报告（results_dir）
      cache/                    行情缓存 + LangGraph 断点（data_cache_dir/checkpoints）
      memory/trading_memory.md  决策记忆（memory_log_path）
      incomplete_tasks.json     未完成任务索引
      missing_data_tasks.json   缺失数据任务索引
      missing_data_cache/       缺失数据重试输出
      llm_config.json           模型选择（不含密钥）
      team_config.json          团队编排 / 提示词 / 战法
      secrets.json              API Key（0600，永不出现在 UI 回显里）
      usage.json                每日运行次数

**uid 由服务端生成**（``u_`` + 12 位 hex），不来自用户输入，所以这里拼接路径不
存在目录穿越；``username`` 只用于展示和登录。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# 旧版单用户目录，也是 CLI 仍在使用的目录；owner 账户继续复用它。
LEGACY_HOME = Path(os.path.expanduser("~")) / ".tradingagents"

# uid 只允许服务端生成的形状；即使将来从磁盘读回账户文件，也先过这层校验。
_UID_RE = re.compile(r"^u_[0-9a-f]{12}$")


def tradingagents_home() -> Path:
    """所有用户态数据的根目录（可用 TRADINGAGENTS_HOME 重定向，便于测试/容器）。"""
    raw = os.environ.get("TRADINGAGENTS_HOME") or str(LEGACY_HOME)
    return Path(raw).expanduser()


@dataclass(frozen=True)
class UserPaths:
    """一个账户独占的目录集合。构造后不会自动建目录（``ensure()`` 才建）。"""

    user_id: str
    root: Path
    # owner 复用旧单用户目录（CLI 与 Web 看同一份历史）；成员在 users/<uid>/。
    legacy: bool = False
    # owner 的旧默认配置允许用环境变量改写这三个路径；保持与 CLI 一致，
    # 成员永远不继承这些环境变量（否则会读到 owner 的目录）。
    _logs_override: Path | None = None
    _cache_override: Path | None = None
    _memory_override: Path | None = None

    @classmethod
    def for_user(cls, user: "Mapping[str, Any]", home: Path | None = None) -> "UserPaths":
        """按账户角色取目录：owner 用旧单用户根，成员用独立子目录。"""
        base = Path(home).expanduser() if home is not None else tradingagents_home()
        user_id = str(user.get("id") or "")
        if not _UID_RE.match(user_id):
            raise ValueError(f"非法用户 ID: {user_id!r}")
        if str(user.get("role") or "") == "owner":
            def _env_path(name: str) -> Path | None:
                raw = os.environ.get(name)
                return Path(raw).expanduser() if raw else None

            return cls(
                user_id=user_id,
                root=base,
                legacy=True,
                _logs_override=_env_path("TRADINGAGENTS_RESULTS_DIR"),
                _cache_override=_env_path("TRADINGAGENTS_CACHE_DIR"),
                _memory_override=_env_path("TRADINGAGENTS_MEMORY_LOG_PATH"),
            )
        return cls(user_id=user_id, root=base / "users" / user_id)

    @classmethod
    def for_user_id(cls, user_id: str, home: Path | None = None) -> "UserPaths":
        """按 ID 取成员目录（测试 / 非 owner 场景）。"""
        if not _UID_RE.match(str(user_id)):
            raise ValueError(f"非法用户 ID: {user_id!r}")
        base = Path(home).expanduser() if home is not None else tradingagents_home()
        return cls(user_id=user_id, root=base / "users" / user_id)

    # ── 固定文件/目录 ───────────────────────────────────────────────────
    @property
    def logs(self) -> Path:
        return self._logs_override or (self.root / "logs")

    @property
    def cache(self) -> Path:
        return self._cache_override or (self.root / "cache")

    @property
    def memory_dir(self) -> Path:
        return self.memory_log.parent

    @property
    def memory_log(self) -> Path:
        return self._memory_override or (self.root / "memory" / "trading_memory.md")

    @property
    def incomplete_tasks(self) -> Path:
        return self.root / "incomplete_tasks.json"

    @property
    def missing_data_tasks(self) -> Path:
        return self.root / "missing_data_tasks.json"

    @property
    def missing_data_cache(self) -> Path:
        return self.root / "missing_data_cache"

    @property
    def llm_config(self) -> Path:
        return self.root / "llm_config.json"

    @property
    def team_config(self) -> Path:
        return self.root / "team_config.json"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets.json"

    @property
    def usage(self) -> Path:
        return self.root / "usage.json"

    def ensure(self) -> "UserPaths":
        """创建目录并收紧权限（0700）。已存在不报错。"""
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            # Windows / 只读文件系统不支持下述位；隔离仍然由路径边界保证。
            pass
        for directory in (
            self.logs,
            self.cache,
            self.memory_dir,
            self.missing_data_cache,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def contains(self, path: Path | str) -> bool:
        """路径是否在这个用户的**报告目录**内。

        不能拿 ``root`` 判：owner 的 root 是 ``~/.tradingagents``，里面有 auth/ 和
        其他账户的 users/；只允许读 logs/，否则构造一个历史路径就能越权读账户库。
        """
        try:
            target = Path(path).expanduser().resolve()
            logs = self.logs.resolve()
        except OSError:
            return False
        try:
            target.relative_to(logs)
        except ValueError:
            return False
        return True

    def disk_usage_bytes(self, *, max_files: int = 20000) -> int:
        """统计用户目录占用（带文件数上限，避免被超大目录拖死 UI）。"""
        total = 0
        scanned = 0
        # owner 的 root 里混着 auth/ 和别人的 users/，不能整棵算；只算自己的
        # 业务目录（与成员目录结构一致）。
        roots = (
            (self.logs, self.cache, self.memory_dir, self.missing_data_cache)
            if self.legacy
            else (self.root,)
        )
        for root in roots:
            try:
                iterator = root.rglob("*")
                for entry in iterator:
                    if scanned >= max_files:
                        break
                    try:
                        if entry.is_file():
                            total += entry.stat().st_size
                            scanned += 1
                    except OSError:
                        continue
            except OSError:
                continue
            if scanned >= max_files:
                break
        if self.legacy:
            for path in (
                self.incomplete_tasks,
                self.missing_data_tasks,
                self.llm_config,
                self.team_config,
                self.secrets,
            ):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                        scanned += 1
                except OSError:
                    continue
        return total
