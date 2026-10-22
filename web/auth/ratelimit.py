"""进程内滑动窗口限流。

Streamlit 默认单进程部署（本项目的 docker-compose 也是），进程内限流足够挡住
脚本化的撞库/批量注册。**多 worker 部署时每个 worker 各算一份**——如果将来要
横向扩展，把这里换成 Redis 实现即可，接口不变。

不持久化：进程重启后计数清零。这是有意的取舍——持久化会引入写放大和文件锁，
而对「在线爆破」这个主要威胁，重启清空只会让攻击者多试几次，仍然受窗口限制。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class RateLimiter:
    """按 key 记录事件时间戳；窗口内事件数达到上限即拒绝。"""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _prune(self, key: str, window: float, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = now - window
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def check(self, key: str, *, limit: int, window: float) -> RateLimitResult:
        """只看不记：现在这个 key 还能不能再来一次。"""
        if limit <= 0:
            return RateLimitResult(False, int(window))
        now = self._clock()
        with self._lock:
            events = self._prune(key, window, now)
            if len(events) >= limit:
                retry = max(1, int(events[0] + window - now) + 1)
                return RateLimitResult(False, retry)
            return RateLimitResult(True, 0)

    def hit(self, key: str, *, limit: int, window: float) -> RateLimitResult:
        """记一次尝试；返回本次是否放行。"""
        if limit <= 0:
            return RateLimitResult(False, int(window))
        now = self._clock()
        with self._lock:
            events = self._prune(key, window, now)
            if len(events) >= limit:
                retry = max(1, int(events[0] + window - now) + 1)
                return RateLimitResult(False, retry)
            events.append(now)
            # 防御性清理：被大量随机 key 打时，别让字典无限增长。
            if len(self._events) > 10000:
                self._compact(now)
            return RateLimitResult(True, 0)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def _compact(self, now: float) -> None:
        # 最长窗口按 1 小时算；更老的 key 对任何判据都没有影响。
        cutoff = now - 3600
        stale = [k for k, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            del self._events[key]


# 全局单例：登录 / 注册 / 邀请码共用。测试可以自己 new 一个。
LIMITER = RateLimiter()


def login_keys(ip: str, username: str) -> tuple[str, str]:
    """返回 (按 IP+用户名, 按 IP) 两个限流键。"""
    return f"login:{ip}:{username.lower()}", f"login-ip:{ip}"


def registration_key(ip: str) -> str:
    return f"register:{ip}"
