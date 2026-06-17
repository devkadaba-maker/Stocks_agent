"""Loads .env and exposes typed settings to all modules."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve paths relative to this file, not the working directory
ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
PROMPTS_DIR = ROOT_DIR / "prompts"

load_dotenv(ROOT_DIR / ".env")


class Settings:
    # --- Paths ---
    ROOT_DIR: Path = ROOT_DIR
    DATA_DIR: Path = DATA_DIR
    LOGS_DIR: Path = LOGS_DIR
    PROMPTS_DIR: Path = PROMPTS_DIR

    # --- API Keys ---
    IBKR_BASE_URL: str = os.getenv("IBKR_BASE_URL", "https://localhost:5000/v1/api")
    IBKR_ACCOUNT_ID: str = os.getenv("IBKR_ACCOUNT_ID", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro")
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # --- Strategy ---
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "10"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "20"))
    MAX_DAILY_TRADES: int = int(os.getenv("MAX_DAILY_TRADES", "5"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "7"))
    ATR_MULTIPLIER: float = float(os.getenv("ATR_MULTIPLIER", "2.0"))
    MIN_CONVICTION_SCORE: int = int(os.getenv("MIN_CONVICTION_SCORE", "6"))
    CLOSE_POSITIONS_EOD: bool = (
        os.getenv("CLOSE_POSITIONS_EOD", "false").lower() == "true"
    )
    CASH_RESERVE_PCT: float = float(os.getenv("CASH_RESERVE_PCT", "15"))
    DEPLOYMENT_CAUTION_PCT: float = float(os.getenv("DEPLOYMENT_CAUTION_PCT", "70"))
    MAX_CAPITAL: float = float(os.getenv("MAX_CAPITAL", "0"))
    REQUIRE_CONFIRMATION: bool = (
        os.getenv("REQUIRE_CONFIRMATION", "true").lower() == "true"
    )

    # --- Entry/exit strictness + holding period ---
    # LOOSE_RULES=true: wider screener thresholds, and the LLM (not a hard
    # stop-loss) decides every exit. LOOSE_RULES=false (default): screener
    # thresholds are tighter, and a hard stop-loss SELL is forced regardless
    # of what the LLM says.
    LOOSE_RULES: bool = os.getenv("LOOSE_RULES", "false").lower() == "true"
    # 0 = no max-hold override (LLM/stop-loss decide exits as usual).
    MAX_HOLD_DAYS: int = int(os.getenv("MAX_HOLD_DAYS", "0"))
    SAVE_DAILY_CHARTS: bool = os.getenv("SAVE_DAILY_CHARTS", "true").lower() == "true"

    # --- Screener ---
    MIN_PRICE: float = float(os.getenv("MIN_PRICE", "1"))
    MAX_PRICE: float = float(os.getenv("MAX_PRICE", "9999"))
    MIN_AVG_VOLUME: int = int(os.getenv("MIN_AVG_VOLUME", "10000"))
    MAX_DAILY_DOLLAR_VOLUME: float = float(os.getenv("MAX_DAILY_DOLLAR_VOLUME", "150000000"))
    SCREEN_SHORTLIST_SIZE: int = int(os.getenv("SCREEN_SHORTLIST_SIZE", "25"))
    SHORTLIST_SIZE: int = int(os.getenv("SHORTLIST_SIZE", "10"))
    SCREEN_DAYS: int = int(os.getenv("SCREEN_DAYS", "120"))
    BACKTEST_MAX_HOLD_DAYS: int = int(os.getenv("BACKTEST_MAX_HOLD_DAYS", "365"))
    BACKTEST_START_DATE: str = os.getenv("BACKTEST_START_DATE", "2018-01-01")

    # --- Schedule (ET) ---
    SCREEN_TIME: str = os.getenv("SCREEN_TIME", "09:30")
    CYCLE_INTERVAL_MINUTES: int = int(os.getenv("CYCLE_INTERVAL_MINUTES", "150"))
    SUMMARY_TIME: str = os.getenv("SUMMARY_TIME", "15:55")

    # --- Derived Paths ---
    SHORTLIST_PATH: Path = DATA_DIR / "shortlist.json"
    VIRTUAL_PORTFOLIO_PATH: Path = DATA_DIR / "virtual_portfolio.json"
    TRADES_DB_PATH: Path = DATA_DIR / "trades.db"
    POSITION_ENTRIES_PATH: Path = DATA_DIR / "position_entries.json"
    EQUITY_LOG_PATH: Path = DATA_DIR / "equity_log.csv"
    EQUITY_CHART_PATH: Path = DATA_DIR / "equity_curve.png"
    LOG_PATH: Path = LOGS_DIR / "agent.log"
    PHASE1_PROMPT_PATH: Path = PROMPTS_DIR / "phase1_prompt.txt"
    PHASE2_PROMPT_PATH: Path = PROMPTS_DIR / "phase2_prompt.txt"
    PHASE1_RISK_PROMPT_PATH: Path = PROMPTS_DIR / "phase1_risk_prompt.txt"
    PHASE2_RISK_PROMPT_PATH: Path = PROMPTS_DIR / "phase2_risk_prompt.txt"
    PHASE1_MADMAX_PROMPT_PATH: Path = PROMPTS_DIR / "phase1_madmax_prompt.txt"
    PHASE2_MADMAX_PROMPT_PATH: Path = PROMPTS_DIR / "phase2_madmax_prompt.txt"

    # --- Extra universes ---
    # Crypto / leveraged ETFs the screener never picks up, but which can be
    # opted into (independently of risk level) on top of the normal stock
    # universe.
    CRYPTO_TICKERS: list = [
        "BTC-USD",
        "ETH-USD",
        "SOL-USD",
        "DOGE-USD",
        "AVAX-USD",
        "ADA-USD",
        "XRP-USD",
    ]
    LEVERAGED_ETF_TICKERS: list = ["TQQQ", "SOXL", "UPRO", "TECL", "FNGU"]
    # Backward-compatible combined list, used by MAD MAX mode.
    MADMAX_TICKERS: list = CRYPTO_TICKERS + LEVERAGED_ETF_TICKERS


settings = Settings()


def is_crypto(symbol: str) -> bool:
    """True for crypto pairs (e.g. BTC-USD, ETH-USD).

    Crypto trades 24/7 and can be bought in fractional units, unlike whole-share
    equities/ETFs. Identified by the "-USD" quote suffix, which all of
    CRYPTO_TICKERS use.
    """
    return bool(symbol) and symbol.upper().endswith("-USD")


def extra_universe_tickers(
    include_crypto: bool = False, include_etfs: bool = False
) -> list[str]:
    """Return the optional crypto / leveraged-ETF tickers to surface as
    candidates on top of the normal stock universe, independent of risk
    level. MAD MAX mode includes both regardless of these flags."""
    tickers: list[str] = []
    if include_crypto:
        tickers += settings.CRYPTO_TICKERS
    if include_etfs:
        tickers += settings.LEVERAGED_ETF_TICKERS
    return tickers
