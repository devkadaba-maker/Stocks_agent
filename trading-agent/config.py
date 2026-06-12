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

    # --- Screener ---
    MIN_PRICE: float = float(os.getenv("MIN_PRICE", "5"))
    MAX_PRICE: float = float(os.getenv("MAX_PRICE", "500"))
    MIN_AVG_VOLUME: int = int(os.getenv("MIN_AVG_VOLUME", "200000"))
    SHORTLIST_SIZE: int = int(os.getenv("SHORTLIST_SIZE", "20"))
    SCREEN_DAYS: int = int(os.getenv("SCREEN_DAYS", "90"))

    # --- Schedule (ET) ---
    SCREEN_TIME: str = os.getenv("SCREEN_TIME", "09:30")
    CYCLE_INTERVAL_MINUTES: int = int(os.getenv("CYCLE_INTERVAL_MINUTES", "150"))
    SUMMARY_TIME: str = os.getenv("SUMMARY_TIME", "15:55")

    # --- Derived Paths ---
    SHORTLIST_PATH: Path = DATA_DIR / "shortlist.json"
    TRADES_DB_PATH: Path = DATA_DIR / "trades.db"
    LOG_PATH: Path = LOGS_DIR / "agent.log"
    PHASE1_PROMPT_PATH: Path = PROMPTS_DIR / "phase1_prompt.txt"
    PHASE2_PROMPT_PATH: Path = PROMPTS_DIR / "phase2_prompt.txt"


settings = Settings()
