"""最小可运行示例 —— 对应 README「10. 启动 CLI」里的那段代码。

`config` 是传给 TradingAgentsGraph 的**覆盖项**字典，不是配置文件：
只写要改的键，其余取 tradingagents/default_config.py 的默认值（#101）。
API key 走 .env（见 README「7. 配置模型」）。运行：python main.py
"""
from dotenv import load_dotenv

from tradingagents.graph.trading_graph import TradingAgentsGraph

load_dotenv()

# ── MiniMax 示例（推荐）─────────────────────────────
config = {
    "llm_provider": "minimax",
    "deep_think_llm": "MiniMax-M2.7",
    "quick_think_llm": "MiniMax-M2.7-highspeed",
    "output_language": "Chinese",
}

# ── DeepSeek 示例 ───────────────────────────────────
# config = {
#     "llm_provider": "deepseek",
#     "deep_think_llm": "deepseek-chat",
#     "quick_think_llm": "deepseek-chat",
#     "output_language": "Chinese",
# }

if __name__ == "__main__":
    ta = TradingAgentsGraph(debug=True, config=config)
    final_state, decision = ta.propagate("688017", "2026-05-12")
    print(decision)
