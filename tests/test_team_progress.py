"""团队编排关掉分析师后，进度条不能永远差 2 格、也不能把未参与的人标进行中。"""

from __future__ import annotations

import pytest

from web.progress import PIPELINE_STAGES, ProgressTracker
from web.runner import _infer_active_stage


@pytest.mark.unit
def test_expected_stages_follow_selected_analysts():
    tracker = ProgressTracker(selected_analysts=["market", "news"])

    expected = tracker.expected_stage_ids()

    assert expected[:2] == ["market", "news"]
    assert "social" not in expected
    assert expected[2:] == ["quality_gate", "debate", "trader", "risk", "pm"]
    assert tracker.is_stage_enabled("market")
    assert not tracker.is_stage_enabled("social")
    # 非分析师阶段永远参与。
    assert tracker.is_stage_enabled("pm")


@pytest.mark.unit
def test_default_selection_keeps_all_stages():
    tracker = ProgressTracker()  # selected_analysts 为空 = 全部 7 位
    assert tracker.expected_stage_ids() == [s["id"] for s in PIPELINE_STAGES]
    assert len(tracker.expected_stage_ids()) == 12


@pytest.mark.unit
def test_infer_active_stage_skips_disabled_analysts():
    tracker = ProgressTracker(selected_analysts=["news", "market"])
    tracker.is_running = True
    _infer_active_stage(tracker)
    assert tracker.current_stage == "news"


@pytest.mark.unit
def test_infer_active_stage_advances_after_disabled_market():
    tracker = ProgressTracker(selected_analysts=["news"])
    tracker.is_running = True
    tracker.mark_stage_done("news", "report")
    _infer_active_stage(tracker)
    assert tracker.current_stage == "quality_gate"


@pytest.mark.unit
def test_infer_active_stage_keeps_current_active():
    """分析师还在取数（stream 持续出 chunk）时，不能把下一个阶段点亮。"""
    tracker = ProgressTracker(selected_analysts=["market", "social"])
    tracker.is_running = True
    tracker.mark_stage_active("market")

    _infer_active_stage(tracker)

    assert tracker.current_stage == "market"
    assert tracker.stage_status("social") == "pending"


@pytest.mark.unit
def test_infer_active_stage_advances_after_done():
    tracker = ProgressTracker(selected_analysts=["market", "social"])
    tracker.is_running = True
    tracker.mark_stage_active("market")
    tracker.mark_stage_done("market", "report")

    _infer_active_stage(tracker)

    assert tracker.current_stage == "social"
