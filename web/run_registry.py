"""分析运行的准入控制：每用户并发、全局并发、每日次数、磁盘配额。

为什么需要它：Streamlit 的 session_state 只在单个浏览器会话里，同一账户开两个
标签页就能并行跑两份分析；恶意用户更可以直接刷「开始分析」。每个分析会起多个
LLM 请求和行情抓取，不限制会把服务器和朋友们的额度一起打爆。

租约（lease）是**进程内**的，进程重启自动清空；每日次数持久化在用户目录，
重启后仍然算数。单进程部署语义明确；多进程部署需要换成 Redis（见 MULTIUSER.md）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from web.user_paths import UserPaths

DEFAULT_MAX_CONCURRENT_RUNS = 4
DEFAULT_MAX_RUNS_PER_DAY = 20
DEFAULT_USER_QUOTA_MB = 2048
# 租约刚创建、线程还没 bind 时给 60 秒宽限；超过后若没有活线程就视为残留。
LEASE_BIND_GRACE_SECONDS = 60


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def max_concurrent_runs() -> int:
    return _env_int("TRADINGAGENTS_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS, low=1, high=64)


def max_runs_per_day() -> int:
    return _env_int("TRADINGAGENTS_MAX_RUNS_PER_DAY", DEFAULT_MAX_RUNS_PER_DAY, low=0, high=10000)


def user_quota_bytes() -> int:
    mb = _env_int("TRADINGAGENTS_USER_QUOTA_MB", DEFAULT_USER_QUOTA_MB, low=0, high=1024 * 1024)
    return mb * 1024 * 1024


@dataclass
class RunLease:
    token: str
    user_id: str
    started_at: float
    # 分析线程的引用。判断残留时看线程是否还活着，而不是只看开始时间——
    # 用户可以把任务暂停数小时，按时间清租约会让同一账户并发跑多个 worker。
    thread: threading.Thread | None = None

    def release(self) -> None:
        release_run(self.token)


_LOCK = threading.RLock()
_LEASES: dict[str, RunLease] = {}


def _read_usage(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_usage(path: Path, usage: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(usage, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 计数失败不应该拦住分析主流程；并发租约仍在内存里兜底。
        pass


def usage_today(paths: UserPaths) -> int:
    usage = _read_usage(paths.usage)
    today = date.today().isoformat()
    if usage.get("date") != today:
        return 0
    try:
        return max(0, int(usage.get("runs") or 0))
    except (TypeError, ValueError):
        return 0


def _increment_usage(paths: UserPaths) -> int:
    today = date.today().isoformat()
    usage = _read_usage(paths.usage)
    try:
        runs = 0 if usage.get("date") != today else max(0, int(usage.get("runs") or 0))
    except (TypeError, ValueError):
        runs = 0
    runs += 1
    _write_usage(paths.usage, {"date": today, "runs": runs, "updated_at": time.time()})
    return runs


def bind_run_thread(token: str | None, thread: threading.Thread) -> None:
    """把租约绑到实际分析线程，供残留判断使用。"""
    if not token:
        return
    with _LOCK:
        lease = _LEASES.get(str(token))
        if lease is not None:
            lease.thread = thread


def _prune_stale(now: float) -> None:
    stale: list[str] = []
    for token, lease in _LEASES.items():
        # 启动失败/线程还没 bind 的宽限窗口。
        if now - lease.started_at < LEASE_BIND_GRACE_SECONDS:
            continue
        if lease.thread is not None and lease.thread.is_alive():
            # 暂停中的任务线程仍然活着，绝不能因为“暂停太久”清掉租约。
            continue
        stale.append(token)
    for token in stale:
        _LEASES.pop(token, None)


def active_run_count(user_id: str | None = None) -> int:
    now = time.time()
    with _LOCK:
        _prune_stale(now)
        if user_id is None:
            return len(_LEASES)
        return sum(1 for lease in _LEASES.values() if lease.user_id == user_id)


def acquire_run(paths: UserPaths, *, is_owner: bool = False) -> tuple[RunLease | None, str]:
    """尝试为一次分析拿租约。返回 ``(lease, "")`` 或 ``(None, 拒绝原因)``。"""
    # owner 负责整台服务器，磁盘配额只约束成员；否则升级时旧的 cache/logs
    # 一旦超过默认 2GB，owner 会被自己的历史数据锁死。
    # 配额扫描可能遍历两万个文件，**必须在全局锁外做**，否则某个大目录会把
    # 其他用户的 acquire/release 一起卡住。
    quota = user_quota_bytes()
    if not is_owner and quota > 0:
        used = paths.disk_usage_bytes()
        if used > quota:
            return None, (
                f"账户数据已占用 {used / 1024 / 1024:.0f} MB，"
                f"超过配额 {quota / 1024 / 1024:.0f} MB；请清理历史报告后再试。"
            )

    with _LOCK:
        now = time.time()
        _prune_stale(now)

        global_limit = max_concurrent_runs()
        if len(_LEASES) >= global_limit:
            return None, f"服务器当前并发分析已满（{global_limit} 个），请稍后再试。"

        if any(lease.user_id == paths.user_id for lease in _LEASES.values()):
            return None, "你已有一个分析在运行中；请等它结束或先在侧栏停止。"

        daily_limit = max_runs_per_day()
        if not is_owner and daily_limit > 0:
            used_today = usage_today(paths)
            if used_today >= daily_limit:
                return None, f"今日分析次数已用完（上限 {daily_limit} 次），请明天再来。"

        token = uuid.uuid4().hex
        lease = RunLease(token=token, user_id=paths.user_id, started_at=now)
        _LEASES[token] = lease
        if not is_owner:
            _increment_usage(paths)
        return lease, ""


def release_run(token: str) -> None:
    with _LOCK:
        _LEASES.pop(str(token), None)
