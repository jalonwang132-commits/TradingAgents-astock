<p align="center"><a href="README.md">简体中文</a> | <b>English</b></p>

<h1 align="center">Jalon A-Share Research Desk</h1>

<p align="center">
  Personal, local-first multi-agent research workstation for China A-shares.<br>
  Seven AI analysts · bull/bear debate · three-way risk debate · Chinese reports.
</p>

> **One line**: run a complete multi-agent research pipeline on your own machine.
> Market data comes from free public endpoints; the LLM uses your own API key;
> reports stay on your disk.

> ⚠️ **For research and learning only. Not investment advice.** This tool does not
> produce entry prices, stop-loss levels, position sizes, or target prices.

| Item | Detail |
|------|--------|
| **Runtime** | Local Web UI / CLI. No Docker, no database. |
| **Python** | >= 3.10 (3.12 / 3.13 recommended) |
| **Data** | mootdx + Tencent + Eastmoney + Sina + 10jqka + CLS + Baidu. Free, no data API key. |
| **LLMs** | DeepSeek / MiniMax / Qwen / GLM / OpenAI / Claude / Gemini / xAI / Ollama / any OpenAI-compatible gateway |
| **License** | Apache-2.0 (see [NOTICE](./NOTICE)) |

---

## What it is

A personal after-market research desk. It sends one A-share ticker through a fixed
pipeline: seven analysts (market, sentiment, news, fundamentals, policy, hot money,
lockup) produce reports, bull and bear debate, a trader proposes a plan, three risk
stances debate, and a portfolio manager gives the final rating with reasons.
Everything runs locally and can be exported as Markdown or PDF.

It is **not** a stock-picking service, a robo-advisor, a data vendor, or a hosted SaaS.

## Highlights

- **Seven analysts**, including three A-share-specific roles: policy, hot money, lockup.
- **Bull vs. bear debate** plus **aggressive / conservative / neutral** risk debate.
- **Chinese reports**, Markdown / PDF export, local history.
- **Editable prompts**: inspect and edit every role's system/user prompt in the Web UI.
- **Strategy packs**: append your own rules to selected roles without touching code.
- **Bring your own model**: 10+ providers, per-role model overrides.
- **Zero data keys**: no Tushare points, no paid market-data subscription.
- **Local-first**: no project server, no telemetry.

## Quick start

```bash
# 0) Check prerequisites
python3 --version && git --version

# 1) Get the code
git clone https://github.com/jalonwang132-commits/TradingAgents-astock.git
cd TradingAgents-astock

# 2) Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 3) Install (use a mirror if PyPI is slow)
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4) Configure one model key
cp .env.example .env               # Windows: copy .env.example .env
# edit .env, e.g. DEEPSEEK_API_KEY=sk-...

# 5) Launch
jalon-web
```

Open <http://localhost:8501>. The full Chinese guide covers environment setup,
Python installation on macOS/Windows/Linux, GitHub/PyPI mirrors, first analysis,
CLI usage, and troubleshooting: **[README.md](README.md)**.

## Common alternatives

```bash
# Start the Web UI without the entry-point script
python -m streamlit run web/app.py

# CLI
jalon
jalon performance

# Minimal Python example
python main.py
```

## Mirrors for slow networks

```bash
# GitHub clone via a mirror prefix (availability varies)
git clone https://ghproxy.net/https://github.com/jalonwang132-commits/TradingAgents-astock.git

# PyPI mirror
python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# uv mirror
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync
```

If `uv` tries to download Python and stalls:

```bash
export UV_PYTHON_DOWNLOADS=never
```

## Requirements

- Python >= 3.10 (3.12/3.13 recommended; 3.9 is not supported).
- At least one LLM API key (DeepSeek, MiniMax, Qwen, GLM, OpenAI, Anthropic, ...).
- Network access to public market-data endpoints and your LLM provider.
- No Docker, Redis, PostgreSQL, or Tushare account required.

## Configuration

Copy `.env.example` to `.env` and fill **one** provider key. The Web sidebar lets
you choose the provider and models. Reports and history are stored under
`~/.tradingagents/`.

Common keys:

| Key | Purpose |
|-----|---------|
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MINIMAX_API_KEY` | MiniMax |
| `DASHSCOPE_API_KEY` | Qwen |
| `ZHIPU_API_KEY` | GLM |
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic / Kimi-compatible endpoint |
| `OPENAI_COMPATIBLE_API_KEY` + `BACKEND_URL` | Any OpenAI-compatible gateway |
| `TRADINGAGENTS_MAX_TOKENS` | Raise output limit if reports are truncated |
| `EM_MIN_INTERVAL` | Slow down Eastmoney requests (default 1.0s) |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Python 3.9` / `requires a different Python` | Install 3.12 and use `python3.12 -m pip` |
| `git clone` stalls | `git clone --depth 1`, use a mirror, or download the ZIP |
| `pip install` is slow | Configure the Tsinghua PyPI mirror |
| `jalon: command not found` | Activate `.venv` and reinstall with `pip install -e .` |
| Port 8501 is busy | `python -m streamlit run web/app.py --server.port 8502` |
| Empty analyst reports | Use a model with reliable tool calling |
| PDF Chinese font error | Install `fonts-noto-cjk` or export Markdown |
| Eastmoney rate limit | Set `EM_MIN_INTERVAL=2` |

## License and attribution

Released under the **Apache License 2.0**. See [LICENSE](./LICENSE).

This is an independent derivative in the open-source TradingAgents ecosystem.
Original copyright notices, modification statements, and full attribution are
retained in [NOTICE](./NOTICE), as required by Apache-2.0. This project is not
affiliated with or endorsed by the maintainers of the original projects.

---

<p align="center">
  <sub>Jalon A-Share Research Desk · local-first · research only</sub>
</p>
