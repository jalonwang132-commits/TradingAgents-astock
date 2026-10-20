"""团队配置持久化 / 校验 / 应用到运行 config 的测试。"""

from __future__ import annotations

import json

import pytest

from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    get_default_prompt,
)
from web.team_store import (
    DEFAULT_MAX_DEBATE_ROUNDS,
    DEFAULT_MAX_RISK_ROUNDS,
    apply_team_config_to_run_config,
    build_agent_prompts,
    default_team_config,
    load_team_config,
    normalize_team_config,
    reset_all_prompts,
    reset_prompt,
    save_team_config,
    set_team_options,
    update_prompt,
)


@pytest.mark.unit
def test_default_config_is_safe():
    cfg = default_team_config()
    assert cfg["version"] == 1
    assert cfg["prompts"] == {}
    assert cfg["selected_analysts"] == list(ANALYST_ROLES)
    assert cfg["max_debate_rounds"] == DEFAULT_MAX_DEBATE_ROUNDS
    assert cfg["max_risk_rounds"] == DEFAULT_MAX_RISK_ROUNDS


@pytest.mark.unit
def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "team_config.json"
    cfg = default_team_config()
    cfg = update_prompt(cfg, "market", "system", "自定义市场提示词")
    cfg = set_team_options(
        cfg,
        selected_analysts=["market", "news"],
        max_debate_rounds=3,
        max_risk_rounds=2,
    )

    saved = save_team_config(cfg, path)
    assert path.exists()
    assert json.loads(path.read_text())["prompts"]["market"]["system"] == "自定义市场提示词"

    loaded = load_team_config(path)
    assert loaded["selected_analysts"] == ["market", "news"]  # 固定目录顺序
    assert loaded["max_debate_rounds"] == 3
    assert loaded["max_risk_rounds"] == 2
    assert loaded["prompts"] == saved["prompts"]


@pytest.mark.unit
def test_load_missing_or_corrupt_returns_defaults(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_team_config(missing) == default_team_config()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_team_config(bad) == default_team_config()


@pytest.mark.unit
def test_normalize_rejects_unknown_roles_and_parts():
    cfg = normalize_team_config(
        {
            "prompts": {
                "market": {"system": "ok", "nope": "drop"},
                "unknown_role": {"system": "drop"},
                "bull": "扁平字符串也应被拒绝",
            },
            "selected_analysts": ["news", "unknown", "market"],
            "max_debate_rounds": 999,
            "max_risk_rounds": -3,
        }
    )

    assert cfg["prompts"] == {"market": {"system": "ok"}}
    assert cfg["selected_analysts"] == ["market", "news"]
    assert cfg["max_debate_rounds"] == 5
    assert cfg["max_risk_rounds"] == 1


@pytest.mark.unit
def test_empty_analyst_selection_falls_back_to_all():
    cfg = normalize_team_config({"selected_analysts": []})
    assert cfg["selected_analysts"] == list(ANALYST_ROLES)


@pytest.mark.unit
def test_update_prompt_adds_and_prunes_defaults():
    cfg = default_team_config()
    cfg = update_prompt(cfg, "trader", "system", "新的系统指令")
    cfg = update_prompt(cfg, "trader", "user", "新的用户模板")

    assert cfg["prompts"]["trader"] == {
        "system": "新的系统指令",
        "user": "新的用户模板",
    }

    # 改回默认值 = 移除覆盖，不留下无意义的 diff。
    cfg = update_prompt(
        cfg, "trader", "system", get_default_prompt("trader", "system")
    )
    assert cfg["prompts"]["trader"] == {"user": "新的用户模板"}

    cfg = update_prompt(cfg, "trader", "user", get_default_prompt("trader", "user"))
    assert cfg["prompts"] == {}


@pytest.mark.unit
def test_update_prompt_rejects_unknown_part():
    with pytest.raises(KeyError):
        update_prompt(default_team_config(), "market", "nope", "x")


@pytest.mark.unit
def test_reset_prompt_and_reset_all():
    cfg = default_team_config()
    cfg = update_prompt(cfg, "market", "system", "m")
    cfg = update_prompt(cfg, "bull", "argument", "b")

    assert reset_prompt(cfg, "market")["prompts"] == {"bull": {"argument": "b"}}
    assert reset_all_prompts(cfg)["prompts"] == {}


@pytest.mark.unit
def test_apply_team_config_to_run_config_keeps_other_keys():
    run = {"llm_provider": "deepseek", "data_cache_dir": "/tmp/x"}
    cfg = default_team_config()
    cfg = update_prompt(cfg, "market", "system", "custom")
    cfg = set_team_options(
        cfg,
        selected_analysts=["market", "social"],
        max_debate_rounds=2,
        max_risk_rounds=3,
    )

    merged = apply_team_config_to_run_config(run, cfg)

    assert merged["llm_provider"] == "deepseek"
    assert merged["data_cache_dir"] == "/tmp/x"
    assert merged["agent_prompts"] == {"market": {"system": "custom"}}
    assert merged["selected_analysts"] == ["market", "social"]
    assert merged["max_debate_rounds"] == 2
    assert merged["max_risk_discuss_rounds"] == 3


@pytest.mark.unit
def test_build_agent_prompts_returns_only_overrides():
    cfg = default_team_config()
    cfg = update_prompt(cfg, "market", "system", "custom")
    assert build_agent_prompts(cfg) == {"market": {"system": "custom"}}


@pytest.mark.unit
def test_rounds_are_clamped_to_supported_range():
    cfg = set_team_options(
        default_team_config(), max_debate_rounds=0, max_risk_rounds=99
    )
    assert cfg["max_debate_rounds"] == 1
    assert cfg["max_risk_rounds"] == 5
