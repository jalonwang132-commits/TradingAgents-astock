#!/usr/bin/env python3
"""贵州茅台(600519) 每日研究报告生成器。

用法:
    uv run python scripts/daily_600519_report.py            # 分析最近交易日并落盘
    uv run python scripts/daily_600519_report.py 2026-10-16 # 指定分析日期

产出（默认 reports/600519/）:
    <日期>/贵州茅台_600519_<日期>.md   当日完整研究报告（分章节）
    <日期>/raw/                      多智能体原始分区报告（分析师/研究/交易/风控）
    <日期>/summary.json              机器可读摘要（信号、耗时、各分区文本）
    latest.md                        最新一期报告副本
    index.md                         历史报告索引
"""

from __future__ import annotations

import json
import shutil
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402
from cli.main import save_report_to_disk  # noqa: E402
from web.branding import APP_NAME  # noqa: E402

TICKER = "600519"
STOCK_NAME = "贵州茅台"
REPORTS_ROOT = ROOT / "reports" / TICKER


def build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-chat"
    config["quick_think_llm"] = "deepseek-chat"
    config["output_language"] = "Chinese"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["data_vendors"] = {
        "core_stock_apis": "a_stock",
        "technical_indicators": "a_stock",
        "fundamental_data": "a_stock",
        "news_data": "a_stock",
        "signal_data": "a_stock",
    }
    return config


def last_trading_day(today: date | None = None) -> str:
    """粗略推算最近交易日：周末回退到周五。"""
    d = today or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def snapshot_basics(trade_date: str) -> str:
    """拉取确定性行情/估值快照（不依赖 LLM），失败则返回空串。"""
    try:
        from tradingagents.dataflows.a_stock import get_fundamentals

        return (get_fundamentals(TICKER, trade_date) or "").strip()
    except Exception as exc:  # 数据源抖动不应导致整份报告失败
        return f"（快照数据获取失败：{exc}）"


def section(title: str, body: str | None) -> str:
    body = (body or "").strip()
    if not body:
        body = "（本期该维度未产出有效内容，可能受数据源或模型调用波动影响。）"
    return f"## {title}\n\n{body}\n"


def build_report(final_state: dict, decision: str, trade_date: str, run_time: str) -> str:
    basics = snapshot_basics(trade_date)
    bull = final_state.get("investment_debate_state", {}).get("bull_history", "")
    bear = final_state.get("investment_debate_state", {}).get("bear_history", "")
    risk = final_state.get("risk_debate_state", {})

    parts = [
        f"# {STOCK_NAME}（{TICKER}）研究报告",
        f"> 分析日期：{trade_date}　|　生成时间：{run_time}　|　"
        f"生成方式：{APP_NAME} 多智能体流水线（7 分析师 → 多空辩论 → 交易 → 三方风控 → 组合决策）",
        "",
        "> ⚠️ 本报告由 AI 自动生成，仅供研究与学习，不构成任何投资建议。",
        "",
        "## 摘要",
        "",
        f"- **组合决策信号**：{decision.strip() or '（未产出）'}",
        f"- **数据口径**：行情与财务数据取自 mootdx / 腾讯财经 / 东方财富 / 新浪 / 同花顺免费直连；"
        f"分析基准日为 {trade_date}",
        "",
        section("一、公司基本面", final_state.get("fundamentals_report")),
        section("二、近期公告与新闻", final_state.get("news_report")),
        section("三、财务与估值指标", basics),
        section(
            "四、行业与政策动态",
            "\n\n".join(
                x
                for x in [
                    (final_state.get("policy_report") or "").strip(),
                    ("**资金面与游资动向**\n\n" + (final_state.get("hot_money_report") or "").strip())
                    if (final_state.get("hot_money_report") or "").strip()
                    else "",
                ]
                if x
            ),
        ),
        section(
            "五、风险因素",
            "\n\n".join(
                x
                for x in [
                    ("**解禁与股东行为**\n\n" + (final_state.get("lockup_report") or "").strip())
                    if (final_state.get("lockup_report") or "").strip()
                    else "",
                    ("**多方论点（Bull）**\n\n" + bull.strip()) if bull.strip() else "",
                    ("**空方论点（Bear）**\n\n" + bear.strip()) if bear.strip() else "",
                    ("**风控三方评估**\n\n"
                     + "\n\n".join(
                         f"- *{label}*：{txt.strip()}"
                         for label, txt in [
                             ("激进", risk.get("aggressive_history", "")),
                             ("保守", risk.get("conservative_history", "")),
                             ("中立", risk.get("neutral_history", "")),
                         ]
                         if (txt or "").strip()
                     ))
                    if any((risk.get(k) or "").strip() for k in
                           ("aggressive_history", "conservative_history", "neutral_history"))
                    else "",
                ]
                if x
            ),
        ),
        section(
            "六、投资要点",
            "\n\n".join(
                x
                for x in [
                    ("**研究经理综合研判**\n\n" + final_state.get("investment_plan", "").strip())
                    if (final_state.get("investment_plan") or "").strip()
                    else "",
                    ("**交易方案**\n\n" + final_state.get("trader_investment_plan", "").strip())
                    if (final_state.get("trader_investment_plan") or "").strip()
                    else "",
                    ("**组合经理最终决策**\n\n" + final_state.get("final_trade_decision", "").strip())
                    if (final_state.get("final_trade_decision") or "").strip()
                    else "",
                ]
                if x
            ),
        ),
        section("附录：技术面与市场结构", final_state.get("market_report")),
        section("附录：舆情与情绪", final_state.get("sentiment_report")),
        "",
        "---",
        "",
        "**免责声明**：本报告由 AI 自动生成，可能存在错误、遗漏或偏差；不构成投资建议、"
        "不提供目标价或买卖点位。投资决策请咨询具备中国证监会资质的专业机构。股市有风险，投资需谨慎。",
        "",
    ]
    return "\n".join(parts)


TOP_SECTIONS = [
    "一、公司基本面",
    "二、近期公告与新闻",
    "三、财务与估值指标",
    "四、行业与政策动态",
    "五、风险因素",
    "六、投资要点",
    "附录：技术面与市场结构",
    "附录：舆情与情绪",
]
TOP_HEADINGS = {"## 摘要"} | {f"## {t}" for t in TOP_SECTIONS}


def normalize_heading_levels(report_md: str) -> str:
    """子报告自带 H1/H2，直接拼进主报告会打乱层级，统一降两级。"""
    out = []
    for i, line in enumerate(report_md.split("\n")):
        if (i == 0 and line.startswith("# ")) or line.strip() in TOP_HEADINGS:
            out.append(line)
            continue
        m = re.match(r"^(#{1,5}) (.+)$", line)
        if m:
            out.append("#" * min(len(m.group(1)) + 2, 6) + " " + m.group(2))
        else:
            out.append(line)
    return "\n".join(out)


def _is_english_block(block: str) -> bool:
    """粗判某个段落是否为整段英文（排除表格、标题、代码块、引用）。"""
    if block.lstrip().startswith(("|", "#", "```", ">")):
        return False
    letters = sum(1 for c in block if c.isascii() and c.isalpha())
    cjk = sum(1 for c in block if "\u4e00" <= c <= "\u9fff")
    return letters > 120 and letters > 2 * cjk


def localize_english_sections(report_md: str, llm, day_dir: Path) -> str:
    """把成稿里的整段英文（风控三方辩论等内部英文智能体产出）等义译为中文。

    内部辩论智能体按设计保留英文推理以保证质量（见 get_language_instruction），
    因此只在报告渲染阶段做一次翻译；任何异常都原样返回，绝不影响报告落盘。
    英文原文与译文留档于 raw/_english_blocks.json。
    """
    blocks = report_md.split("\n\n")
    targets = [i for i, b in enumerate(blocks) if _is_english_block(b)]
    if not targets:
        return report_md

    archive = []
    for idx in targets:
        src = blocks[idx]
        try:
            msg = llm.invoke(
                [
                    (
                        "system",
                        "你是专业的 A 股证券研究报告译者。把用户给的英文段落完整译为简体中文："
                        "保留全部数字、百分比、股票代码、机构名与专有名词，保留原有的分点、"
                        "破折号与段落结构，语气保持原文的论辩强度。只输出译文，不要任何说明。",
                    ),
                    ("human", src),
                ]
            )
            zh = (msg.content if hasattr(msg, "content") else str(msg)).strip()
            if not zh or _is_english_block(zh):
                raise ValueError("译文仍为英文或为空")
            blocks[idx] = zh
            archive.append({"index": idx, "en": src, "zh": zh})
        except Exception as exc:  # 翻译失败保留英文原文，报告照常交付
            print(f"⚠️ 英文段落翻译失败（保留原文）：{exc}")

    if archive:
        try:
            (day_dir / "raw").mkdir(parents=True, exist_ok=True)
            (day_dir / "raw" / "_english_blocks.json").write_text(
                json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (day_dir / "raw" / "_pre_postprocess.md").write_text(report_md, encoding="utf-8")
        except Exception as exc:
            print(f"⚠️ 英文原文留档失败（不影响报告）：{exc}")
        print(f"✅ 已中译英文段落 {len(archive)}/{len(targets)} 处")

    return "\n\n".join(blocks)


def write_index(root: Path) -> None:
    rows = []
    for d in sorted([p for p in root.iterdir() if p.is_dir()], reverse=True):
        md = next(iter(sorted(d.glob("*.md"))), None)
        signal = ""
        summary_path = d / "summary.json"
        if summary_path.exists():
            try:
                signal = json.loads(summary_path.read_text(encoding="utf-8")).get("signal", "")
            except Exception:
                signal = ""
        if md:
            rows.append(f"| {d.name} | [{md.name}]({d.name}/{md.name}) | {signal.strip()[:40] or '—'} |")
    body = "\n".join(rows) if rows else "| — | — | — |"
    (root / "index.md").write_text(
        f"# {STOCK_NAME}（{TICKER}）研究报告历史索引\n\n"
        f"共 {len(rows)} 期，最新在上。\n\n"
        "| 日期 | 报告 | 决策信号 |\n|------|------|----------|\n" + body + "\n",
        encoding="utf-8",
    )


def main() -> int:
    trade_date = sys.argv[1] if len(sys.argv) > 1 else last_trading_day()
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day_dir = REPORTS_ROOT / trade_date
    day_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{run_time}] 开始分析 {STOCK_NAME}({TICKER})，基准日 {trade_date}")
    started = time.time()

    try:
        graph = TradingAgentsGraph(debug=False, config=build_config())
        final_state, decision = graph.propagate(TICKER, trade_date)
    except Exception as exc:
        traceback.print_exc()
        (day_dir / "error.log").write_text(
            f"{run_time}\n{exc}\n\n{traceback.format_exc()}", encoding="utf-8"
        )
        print(f"❌ 分析失败：{exc}")
        return 1

    elapsed = time.time() - started
    print(f"✅ 流水线完成，耗时 {elapsed:.0f}s，信号：{decision}")

    raw_dir = day_dir / "raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    try:
        save_report_to_disk(final_state, TICKER, raw_dir)
    except Exception as exc:
        print(f"⚠️ 原始分区落盘失败（不影响主报告）：{exc}")

    report_md = build_report(final_state, decision, trade_date, run_time)
    report_md = normalize_heading_levels(report_md)
    try:
        report_md = localize_english_sections(report_md, graph.quick_thinking_llm, day_dir)
    except Exception as exc:
        print(f"⚠️ 英文段落中译步骤异常（保留原文）：{exc}")
    report_path = day_dir / f"{STOCK_NAME}_{TICKER}_{trade_date}.md"
    report_path.write_text(report_md, encoding="utf-8")

    (day_dir / "summary.json").write_text(
        json.dumps(
            {
                "ticker": TICKER,
                "name": STOCK_NAME,
                "trade_date": trade_date,
                "run_time": run_time,
                "duration_seconds": round(elapsed),
                "signal": decision,
                "report_file": report_path.name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    shutil.copyfile(report_path, REPORTS_ROOT / "latest.md")
    write_index(REPORTS_ROOT)

    print(f"📄 报告已生成：{report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
