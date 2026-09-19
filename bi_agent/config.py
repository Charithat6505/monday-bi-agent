"""Central configuration.

Values are read (in order) from:
  1. real environment variables
  2. a local `.env` file (development only, git-ignored)
  3. Streamlit secrets (Streamlit Community Cloud)

Secrets never live in the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

try:  # python-dotenv is only needed for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _get(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    try:  # Streamlit Community Cloud secrets
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:  # streamlit missing, or no secrets file present
        pass
    return default


@dataclass(frozen=True)
class Settings:
    monday_token: Optional[str] = None
    gemini_key: Optional[str] = None
    gemini_model: str = "gemini-3.6-flash"  # confirmed/updated when we build the agent step
    work_orders_board_id: Optional[str] = None  # optional override; otherwise found by board name
    deals_board_id: Optional[str] = None  # optional override; otherwise found by board name
    monday_api_version: Optional[str] = None  # unset = monday's "current" version
    cache_ttl_seconds: int = 300

    def missing_for_monday(self) -> List[str]:
        return [] if self.monday_token else ["MONDAY_API_TOKEN"]

    def missing_for_agent(self) -> List[str]:
        missing = self.missing_for_monday()
        if not self.gemini_key:
            missing.append("GEMINI_API_KEY")
        return missing


def load_settings() -> Settings:
    try:
        ttl = int(_get("CACHE_TTL_SECONDS", "300") or 300)
    except ValueError:
        ttl = 300
    return Settings(
        monday_token=_get("MONDAY_API_TOKEN"),
        gemini_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash",
        work_orders_board_id=_get("WORK_ORDERS_BOARD_ID"),
        deals_board_id=_get("DEALS_BOARD_ID"),
        monday_api_version=_get("MONDAY_API_VERSION"),
        cache_ttl_seconds=ttl,
    )
