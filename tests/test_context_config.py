"""运行期配置的线程隔离（多用户并发的核心防线）。

回归的是：`dataflows/config.py` 曾是进程级全局 dict，两个分析线程同时跑时后启动
的那次会覆盖前一次的 strategy_packs / output_language / 缓存目录，A 的战法会进 B
的提示词。现在用 ContextVar，每个线程各一份。
"""

from __future__ import annotations

import threading

from tradingagents.dataflows.config import get_config, reset_config, set_config


def _restore(saved: dict) -> None:
    reset_config()
    set_config(saved)


def test_main_thread_config_does_not_leak_into_worker_threads():
    saved = dict(get_config())
    try:
        set_config({"market_lookback_days": 7, "strategy_packs": [{"id": "main"}]})

        seen: dict[str, object] = {}

        def worker() -> None:
            seen["lookback"] = get_config().get("market_lookback_days")
            seen["packs"] = get_config().get("strategy_packs")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        # 新线程拿到 DEFAULT_CONFIG，而不是主线程的覆盖值。
        assert seen["lookback"] in (None, 30)
        assert seen["packs"] == []
    finally:
        _restore(saved)


def test_two_workers_keep_their_own_strategy_packs():
    saved = dict(get_config())
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            set_config({"strategy_packs": [{"id": name, "enabled": True}]})
            barrier.wait(timeout=5)
            results[name] = get_config()["strategy_packs"][0]["id"]
        except BaseException as exc:  # pragma: no cover - 失败时给 pytest 看
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert results == {"A": "A", "B": "B"}
    finally:
        _restore(saved)


def test_set_config_merges_instead_of_replacing():
    saved = dict(get_config())
    try:
        set_config({"llm_provider": "deepseek"})
        set_config({"output_language": "English"})
        cfg = get_config()
        assert cfg["llm_provider"] == "deepseek"
        assert cfg["output_language"] == "English"
        # 未覆盖的键保留默认值
        assert "results_dir" in cfg
    finally:
        _restore(saved)


def test_reset_config_returns_defaults():
    saved = dict(get_config())
    try:
        set_config({"llm_provider": "deepseek", "user_home": "/tmp/x"})
        reset_config()
        cfg = get_config()
        assert cfg.get("llm_provider") == "openai"
        assert "user_home" not in cfg
    finally:
        _restore(saved)
