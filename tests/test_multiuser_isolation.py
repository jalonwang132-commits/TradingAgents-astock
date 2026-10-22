"""多用户数据隔离：路径、团队配置、历史、密钥、运行准入。"""

from __future__ import annotations

import json
import os
import threading

import pytest

from tradingagents.dataflows import missing_data
from tradingagents.dataflows.config import get_config, reset_config, set_config
from web import history
from web.auth.store import AuthStore
from web.run_registry import (
    acquire_run,
    active_run_count,
    bind_run_thread,
    release_run,
    usage_today,
)
from web.secrets_store import load_secrets, set_api_key
from web.team_store import (
    default_team_config,
    get_strategy_pack,
    load_team_config,
    add_strategy_pack,
    save_team_config,
    set_team_options,
)
from web.user_context import activate, current_paths, deactivate
from web.user_paths import UserPaths

PASSWORD = "correct-horse-1"


@pytest.fixture()
def two_users(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    monkeypatch.setenv("TRADINGAGENTS_MAX_USERS", "10")
    store = AuthStore(tmp_path)
    owner = store.create_owner("owner", PASSWORD, "老板")
    _invite, code = store.create_invite(owner["id"])
    member = store.register(code, "friend_a", PASSWORD, "朋友A")
    owner_paths = UserPaths.for_user(owner).ensure()
    member_paths = UserPaths.for_user(member).ensure()
    try:
        yield store, owner, member, owner_paths, member_paths
    finally:
        deactivate()


@pytest.mark.unit
def test_user_paths_reject_traversal(tmp_path):
    with pytest.raises(ValueError):
        UserPaths.for_user_id("../../etc", home=tmp_path)
    paths = UserPaths.for_user_id("u_0123456789ab", home=tmp_path)
    assert paths.contains(paths.logs / "600519" / "x.json")
    assert not paths.contains(tmp_path / "other")


@pytest.mark.unit
def test_history_and_incomplete_tasks_are_per_user(two_users, tmp_path):
    _store, _owner, _member, owner_paths, member_paths = two_users

    activate(owner_paths)
    log_dir = owner_paths.logs / "600519" / "TradingAgentsStrategy_logs"
    log_dir.mkdir(parents=True)
    (log_dir / "full_states_log_2026-05-12.json").write_text(
        json.dumps({"final_trade_decision": "买入"}), encoding="utf-8"
    )
    history.record_incomplete_task("600519", "2026-05-13", status="paused")
    assert [e["ticker"] for e in history.get_history()] == ["600519"]
    assert [e["ticker"] for e in history.get_incomplete_history()] == ["600519"]

    activate(member_paths)
    assert history.get_history() == []
    assert history.get_incomplete_history() == []
    history.record_incomplete_task("000001", "2026-05-14", status="running")
    assert [e["ticker"] for e in history.get_incomplete_history()] == ["000001"]
    assert not (member_paths.logs / "600519").exists()

    activate(owner_paths)
    assert [e["ticker"] for e in history.get_history()] == ["600519"]
    assert [e["ticker"] for e in history.get_incomplete_history()] == ["600519"]


@pytest.mark.unit
def test_load_analysis_rejects_other_users_path(two_users):
    _store, _owner, _member, owner_paths, member_paths = two_users
    target = owner_paths.logs / "600519" / "TradingAgentsStrategy_logs" / "full_states_log_2026-05-12.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"x": 1}), encoding="utf-8")

    activate(member_paths)
    with pytest.raises(ValueError):
        history.load_analysis(str(target))

    activate(owner_paths)
    assert history.load_analysis(str(target)) == {"x": 1}


@pytest.mark.unit
def test_team_config_is_per_user(two_users):
    _store, _owner, _member, owner_paths, member_paths = two_users

    activate(owner_paths)
    owner_cfg = set_team_options(default_team_config(), max_debate_rounds=3)
    owner_cfg = add_strategy_pack(
        owner_cfg, name="老板战法", scope=["market"], content="只看月线"
    )
    save_team_config(owner_cfg)
    assert load_team_config()["max_debate_rounds"] == 3

    activate(member_paths)
    member_cfg = set_team_options(default_team_config(), max_debate_rounds=1)
    member_cfg = add_strategy_pack(
        member_cfg, name="朋友战法", scope=[], content="控制回撤"
    )
    save_team_config(member_cfg)
    loaded = load_team_config()
    assert loaded["max_debate_rounds"] == 1
    assert [p["name"] for p in loaded["strategy_packs"]] == ["朋友战法"]
    assert get_strategy_pack(loaded, loaded["strategy_packs"][0]["id"])["content"] == "控制回撤"

    # 文件确实分开放
    assert (owner_paths.team_config).exists()
    assert (member_paths.team_config).exists()
    assert "老板战法" in owner_paths.team_config.read_text()
    assert "老板战法" not in member_paths.team_config.read_text()


@pytest.mark.unit
def test_api_keys_are_per_user_and_private(two_users):
    _store, _owner, _member, owner_paths, member_paths = two_users

    set_api_key(owner_paths.secrets, "deepseek", "sk-owner-123456")
    set_api_key(member_paths.secrets, "deepseek", "sk-member-abcdef")

    assert load_secrets(owner_paths.secrets)["deepseek"] == "sk-owner-123456"
    assert load_secrets(member_paths.secrets)["deepseek"] == "sk-member-abcdef"
    assert "sk-owner" not in member_paths.secrets.read_text()
    if os.name == "posix":
        assert member_paths.secrets.stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_activate_binds_dataflow_paths_and_missing_data(two_users):
    _store, _owner, _member, owner_paths, member_paths = two_users

    activate(owner_paths)
    cfg = get_config()
    assert cfg["results_dir"] == str(owner_paths.logs)
    assert cfg["data_cache_dir"] == str(owner_paths.cache)
    assert cfg["memory_log_path"] == str(owner_paths.memory_log)
    task = missing_data.record_tool_result(
        ticker="600519",
        trade_date="2026-05-12",
        stage="market",
        tool_name="get_stock_data",
        args={"ticker": "600519"},
        content="[数据缺失: K线]",
    )
    assert task is not None
    assert owner_paths.missing_data_tasks.exists()

    activate(member_paths)
    assert get_config()["results_dir"] == str(member_paths.logs)
    assert missing_data.get_missing_tasks("600519", "2026-05-12") == []
    missing_data.record_tool_result(
        ticker="000001",
        trade_date="2026-05-12",
        stage="market",
        tool_name="get_stock_data",
        args={"ticker": "000001"},
        content="[数据缺失: K线]",
    )
    assert member_paths.missing_data_tasks.exists()
    assert "600519" not in member_paths.missing_data_tasks.read_text()


@pytest.mark.unit
def test_deactivate_clears_context(two_users):
    _store, _owner, _member, owner_paths, _member_paths = two_users
    activate(owner_paths)
    assert current_paths() == owner_paths
    deactivate()
    assert current_paths() is None
    reset_config()


@pytest.mark.unit
def test_run_registry_one_concurrent_run_per_user(two_users, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_CONCURRENT_RUNS", "4")
    _store, _owner, _member, owner_paths, member_paths = two_users

    lease, reason = acquire_run(owner_paths, is_owner=True)
    assert lease is not None and reason == ""
    second, refusal = acquire_run(owner_paths, is_owner=True)
    assert second is None and "已有" in refusal

    other, _ = acquire_run(member_paths, is_owner=False)
    assert other is not None
    assert active_run_count() == 2

    release_run(lease.token)
    release_run(other.token)
    assert active_run_count() == 0


@pytest.mark.unit
def test_alive_paused_thread_keeps_its_lease(two_users):
    """暂停中的 worker 线程还活着，不能按开始时间清租约；否则可并发绕限。"""
    _store, _owner, _member, owner_paths, _member_paths = two_users
    lease, _ = acquire_run(owner_paths, is_owner=True)
    assert lease is not None
    # 模拟暂停了几个小时。
    lease.started_at -= 10_000

    gate = threading.Event()
    thread = threading.Thread(target=gate.wait, args=(5,), daemon=True)
    bind_run_thread(lease.token, thread)
    thread.start()
    try:
        assert active_run_count(owner_paths.user_id) == 1
        blocked, refusal = acquire_run(owner_paths, is_owner=True)
        assert blocked is None and "已有" in refusal
    finally:
        gate.set()
        thread.join(timeout=5)
        release_run(lease.token)
    assert active_run_count(owner_paths.user_id) == 0


@pytest.mark.unit
def test_run_registry_daily_limit_applies_to_members_only(two_users, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_RUNS_PER_DAY", "1")
    _store, _owner, _member, owner_paths, member_paths = two_users

    first, _ = acquire_run(member_paths, is_owner=False)
    assert first is not None
    release_run(first.token)
    assert usage_today(member_paths) == 1

    denied, refusal = acquire_run(member_paths, is_owner=False)
    assert denied is None and "今日" in refusal

    for _ in range(3):
        lease, _ = acquire_run(owner_paths, is_owner=True)
        assert lease is not None
        release_run(lease.token)


@pytest.mark.unit
def test_global_concurrency_cap(two_users, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_CONCURRENT_RUNS", "1")
    _store, _owner, _member, owner_paths, member_paths = two_users

    lease, _ = acquire_run(owner_paths, is_owner=True)
    assert lease is not None
    blocked, refusal = acquire_run(member_paths, is_owner=False)
    assert blocked is None and "并发" in refusal
    release_run(lease.token)


@pytest.mark.unit
def test_validate_model_selection_blocks_member_without_key(monkeypatch):
    from web.components.sidebar import validate_model_selection

    config = {
        "quick_think_llm": "deepseek-chat",
        "deep_think_llm": "deepseek-chat",
        "llm_provider": "deepseek",
        "llm_api_keys": {},
        "allow_env_api_keys": False,
    }
    assert "API Key" in validate_model_selection(config)

    config["llm_api_keys"] = {"deepseek": "sk-test"}
    assert validate_model_selection(config) is None

    config["allow_env_api_keys"] = True
    config["llm_api_keys"] = {}
    # owner 不硬拦：可能走 Claude 订阅覆盖，由客户端在真正需要时报错。
    assert validate_model_selection(config) is None


@pytest.mark.unit
def test_missing_data_defaults_still_legacy_without_binding(tmp_path, monkeypatch):
    # 未绑定用户（CLI / 旧单用户）仍写旧路径，行为不变。
    index = tmp_path / "missing_data_tasks.json"
    cache = tmp_path / "missing_data_cache"
    monkeypatch.setattr(missing_data, "_MISSING_DATA_TASKS_FILE", index)
    monkeypatch.setattr(missing_data, "_MISSING_DATA_CACHE_DIR", cache)
    missing_data.set_store_root(None)
    missing_data.record_tool_result(
        ticker="600519",
        trade_date="2026-05-12",
        stage="market",
        tool_name="get_stock_data",
        args={"ticker": "600519"},
        content="[数据缺失: K线]",
    )
    assert index.exists()
