"""Loads the two boards from monday.com (always live) and caches them briefly.

* Boards are found by name ("Work Orders", "Deals") unless board IDs are set in config.
* Results are cached for CACHE_TTL_SECONDS so a chat session doesn't hammer the API.
* If monday.com fails but we hold an earlier copy, we serve it flagged as stale so the
  agent can tell the user, instead of the whole app going down.
* Nothing here reads a CSV: every answer is built from data fetched from monday.com.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Dict, Optional

from .config import Settings, load_settings
from .monday_client import BoardData, MondayClient, MondayError, MondayNotFoundError

logger = logging.getLogger(__name__)

KINDS = ("work_orders", "deals")

_LOCK = threading.Lock()  # one workspace, shared by all chat sessions
_CACHE: Dict[str, "Loaded"] = {}


@dataclass
class Loaded:
    board: BoardData
    fetched_at: float
    stale: bool = False
    warning: Optional[str] = None


def _matches(kind: str, board_name: str) -> bool:
    name = " ".join(board_name.replace("_", " ").replace("-", " ").lower().split())
    if name.startswith("subitems of"):
        return False
    if kind == "work_orders":
        return "work order" in name
    return "deal" in name and "work order" not in name


def resolve_board_id(client: MondayClient, settings: Settings, kind: str) -> str:
    override = settings.work_orders_board_id if kind == "work_orders" else settings.deals_board_id
    if override:
        return str(override)
    boards = client.list_boards()
    matches = [b for b in boards if _matches(kind, b["name"])]
    if not matches:
        names = ", ".join(b["name"] for b in boards) or "none"
        raise MondayNotFoundError(
            f"Could not find a '{kind.replace('_', ' ')}' board. Boards this token can see: {names}. "
            "Name the boards 'Work Orders' and 'Deals', or set WORK_ORDERS_BOARD_ID / DEALS_BOARD_ID."
        )
    if len(matches) > 1:
        logger.warning("Several boards match %s; using '%s'", kind, matches[0]["name"])
    return matches[0]["id"]


def load_board(
    kind: str,
    force_refresh: bool = False,
    settings: Optional[Settings] = None,
    client: Optional[MondayClient] = None,
) -> Loaded:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    settings = settings or load_settings()
    with _LOCK:
        cached = _CACHE.get(kind)
        fresh = cached is not None and (time.time() - cached.fetched_at) < settings.cache_ttl_seconds
        if cached and fresh and not force_refresh:
            return cached
        try:
            client = client or MondayClient(settings.monday_token, settings.monday_api_version)
            board_id = resolve_board_id(client, settings, kind)
            board = client.fetch_board(board_id)
        except MondayError as exc:
            if cached:
                stale = replace(
                    cached,
                    stale=True,
                    warning=f"Could not refresh from monday.com ({exc}). Showing data fetched earlier.",
                )
                _CACHE[kind] = stale
                return stale
            raise
        loaded = Loaded(board=board, fetched_at=time.time())
        _CACHE[kind] = loaded
        return loaded


def load_all(force_refresh: bool = False, settings: Optional[Settings] = None) -> Dict[str, Loaded]:
    settings = settings or load_settings()
    client = MondayClient(settings.monday_token, settings.monday_api_version)
    return {k: load_board(k, force_refresh, settings, client) for k in KINDS}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()