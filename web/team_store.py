"""「投研团队」配置的磁盘持久化。

与侧栏的 LLM 配置分开存：团队编排属于**项目级**偏好（哪些分析师参与、辩论几轮、
每个角色的提示词），不应该因为换了个模型就被覆盖。

文件位置：``~/.tradingagents/team_config.json``。读取时做一次结构校验，坏文件
回落到默认值而不是让整个页面崩掉；写入走临时文件 + ``os.replace``，避免写一半
被 Ctrl-C 打断后留下半个 JSON。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from tradingagents.agents.prompt_registry import (
    ANALYST_ROLES,
    DEFAULT_SELECTED_ANALYSTS,
    ROLE_SPECS,
    get_default_prompt,
)

logger = logging.getLogger(__name__)

TEAM_CONFIG_VERSION = 1
TEAM_CONFIG_PATH = (
    Path(os.path.expanduser("~")) / ".tradingagents" / "team_config.json"
)

DEFAULT_MAX_DEBATE_ROUNDS = 1
DEFAULT_MAX_RISK_ROUNDS = 1
_MAX_ROUNDS = 5


def default_team_config() -> dict[str, Any]:
    """全新用户的团队配置。"""
    return {
        "version": TEAM_CONFIG_VERSION,
        "prompts": {},
        "selected_analysts": list(DEFAULT_SELECTED_ANALYSTS),
        "max_debate_rounds": DEFAULT_MAX_DEBATE_ROUNDS,
        "max_risk_rounds": DEFAULT_MAX_RISK_ROUNDS,
        "updated_at": None,
    }


def _clamp_rounds(value: Any, default: int) -> int:
    try:
        rounds = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(_MAX_ROUNDS, rounds))


def _normalize_analysts(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return list(DEFAULT_SELECTED_ANALYSTS)
    selected = {str(item) for item in value}
    # 保持固定顺序，避免图里的分析师顺序随 JSON 里的顺序漂移。
    analysts = [role for role in ANALYST_ROLES if role in selected]
    # 一个都不选会让 GraphSetup 直接报错；回落成全选更友好。
    return analysts or list(DEFAULT_SELECTED_ANALYSTS)


def _normalize_prompts(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, dict[str, str]] = {}
    for role, parts in value.items():
        spec = ROLE_SPECS.get(str(role))
        if spec is None or not isinstance(parts, Mapping):
            continue
        role_parts: dict[str, str] = {}
        for part in spec.parts:
            raw = parts.get(part.key)
            if not isinstance(raw, str):
                continue
            text = raw.strip()
            # 与默认值完全一致就不必落盘，文件保持干净、也便于看出改了什么。
            if text and text != get_default_prompt(role, part.key).strip():
                role_parts[part.key] = text
        if role_parts:
            cleaned[role] = role_parts
    return cleaned


def normalize_team_config(raw: Any) -> dict[str, Any]:
    """把磁盘/导入的任意 dict 校验成页面能安全消费的形状。"""
    cfg = default_team_config()
    if not isinstance(raw, Mapping):
        return cfg

    cfg["prompts"] = _normalize_prompts(raw.get("prompts"))
    cfg["selected_analysts"] = _normalize_analysts(raw.get("selected_analysts"))
    cfg["max_debate_rounds"] = _clamp_rounds(
        raw.get("max_debate_rounds"), DEFAULT_MAX_DEBATE_ROUNDS
    )
    cfg["max_risk_rounds"] = _clamp_rounds(
        raw.get("max_risk_rounds"), DEFAULT_MAX_RISK_ROUNDS
    )
    updated = raw.get("updated_at")
    cfg["updated_at"] = updated if isinstance(updated, str) else None
    return cfg


def load_team_config(path: Path | str | None = None) -> dict[str, Any]:
    """读取团队配置；文件不存在/损坏都返回默认值。"""
    target = Path(path) if path is not None else TEAM_CONFIG_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_team_config()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("团队配置读取失败，使用默认值: %s", exc)
        return default_team_config()
    return normalize_team_config(raw)


def save_team_config(
    config: Mapping[str, Any], path: Path | str | None = None
) -> dict[str, Any]:
    """校验后原子写入，返回真正落盘的配置。"""
    target = Path(path) if path is not None else TEAM_CONFIG_PATH
    normalized = normalize_team_config(config)
    normalized["version"] = TEAM_CONFIG_VERSION

    payload = copy.deepcopy(normalized)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, target)
    return normalized


# ── 纯数据操作（页面和测试共用，不碰 Streamlit） ───────────────────────────


def build_agent_prompts(team_config: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """转成 ``config["agent_prompts"]`` 的形状。"""
    return _normalize_prompts(team_config.get("prompts"))


def apply_team_config_to_run_config(
    run_config: dict[str, Any], team_config: Mapping[str, Any]
) -> dict[str, Any]:
    """把团队编排合并进真正发给 ``TradingAgentsGraph`` 的 config。

    只改这几个键，其余运行配置（模型、缓存目录、订阅覆盖……）原样保留。
    """
    run_config["agent_prompts"] = build_agent_prompts(team_config)
    run_config["selected_analysts"] = list(
        team_config.get("selected_analysts") or DEFAULT_SELECTED_ANALYSTS
    )
    run_config["max_debate_rounds"] = _clamp_rounds(
        team_config.get("max_debate_rounds"), DEFAULT_MAX_DEBATE_ROUNDS
    )
    run_config["max_risk_discuss_rounds"] = _clamp_rounds(
        team_config.get("max_risk_rounds"), DEFAULT_MAX_RISK_ROUNDS
    )
    return run_config


def update_prompt(
    team_config: Mapping[str, Any],
    role: str,
    part: str,
    text: str,
) -> dict[str, Any]:
    """返回更新后的配置副本；把提示词改回默认值时自动移除覆盖。"""
    cfg = normalize_team_config(team_config)
    prompts = {r: dict(p) for r, p in cfg["prompts"].items()}
    spec = ROLE_SPECS[role]
    if part not in {p.key for p in spec.parts}:
        raise KeyError(f"角色 {role!r} 没有提示词片段 {part!r}")

    cleaned = (text or "").strip()
    if cleaned and cleaned != get_default_prompt(role, part).strip():
        prompts.setdefault(role, {})[part] = text
    else:
        if role in prompts:
            prompts[role].pop(part, None)
            if not prompts[role]:
                del prompts[role]
    cfg["prompts"] = prompts
    return cfg


def reset_prompt(
    team_config: Mapping[str, Any], role: str, part: str | None = None
) -> dict[str, Any]:
    """恢复某个角色的一个片段或全部提示词。"""
    cfg = normalize_team_config(team_config)
    prompts = {r: dict(p) for r, p in cfg["prompts"].items()}
    if role in prompts:
        if part is None:
            del prompts[role]
        else:
            prompts[role].pop(part, None)
            if not prompts[role]:
                del prompts[role]
    cfg["prompts"] = prompts
    return cfg


def reset_all_prompts(team_config: Mapping[str, Any]) -> dict[str, Any]:
    cfg = normalize_team_config(team_config)
    cfg["prompts"] = {}
    return cfg


def set_team_options(
    team_config: Mapping[str, Any],
    *,
    selected_analysts: list[str] | None = None,
    max_debate_rounds: int | None = None,
    max_risk_rounds: int | None = None,
) -> dict[str, Any]:
    cfg = normalize_team_config(team_config)
    if selected_analysts is not None:
        cfg["selected_analysts"] = _normalize_analysts(selected_analysts)
    if max_debate_rounds is not None:
        cfg["max_debate_rounds"] = _clamp_rounds(
            max_debate_rounds, DEFAULT_MAX_DEBATE_ROUNDS
        )
    if max_risk_rounds is not None:
        cfg["max_risk_rounds"] = _clamp_rounds(
            max_risk_rounds, DEFAULT_MAX_RISK_ROUNDS
        )
    return cfg
