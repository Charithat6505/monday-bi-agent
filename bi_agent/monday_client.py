"""Read-only monday.com GraphQL client.

Design points (all worth mentioning in the Decision Log):
  * Direct GraphQL API with a personal token (simpler than MCP for a hosted app).
  * Personal tokens carry every permission scope, so "read-only" is enforced HERE:
    any query that is not a plain `query` is rejected before it leaves the process.
  * Cursor pagination (items_page -> next_items_page) so boards of any size load fully.
  * Retries with backoff for timeouts, HTTP 5xx and rate/complexity limits.
  * Every failure is mapped to a MondayError whose message is safe to show a user.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

API_URL = "https://api.monday.com/v2"
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 100  # safety valve: 100 pages x 100 items

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- errors
class MondayError(Exception):
    """Base error. str(exc) is written to be shown directly to a user."""


class MondayAuthError(MondayError):
    pass


class MondayNotFoundError(MondayError):
    pass


class ReadOnlyViolation(MondayError):
    pass


class MondayRetryableError(MondayError):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class MondayRateLimitError(MondayRetryableError):
    pass


class MondayTransientError(MondayRetryableError):
    pass


_AUTH_CODES = {"UserUnauthorizedException", "Unauthorized"}
_NOT_FOUND_CODES = {"ResourceNotFoundException", "InvalidBoardIdException", "InvalidItemIdException"}
_RATE_CODES = {
    "ComplexityException",
    "RateLimitExceeded",
    "DAILY_LIMIT_EXCEEDED",
    "IP_RATE_LIMIT_EXCEEDED",
    "maxConcurrencyExceeded",
}


# ------------------------------------------------------------------- read-only guard
_COMMENT_RE = re.compile(r"#[^\n]*")
_WRITE_RE = re.compile(r"\b(mutation|subscription)\b", re.IGNORECASE)


def assert_read_only(query: str) -> None:
    """Raise ReadOnlyViolation unless `query` is a plain GraphQL query."""
    cleaned = _COMMENT_RE.sub("", query).strip()
    is_query = cleaned.startswith("query") or cleaned.startswith("{")
    if _WRITE_RE.search(cleaned) or not is_query:
        raise ReadOnlyViolation(
            "Blocked: this agent is read-only and only sends GraphQL queries to monday.com."
        )


# -------------------------------------------------------------------------- models
@dataclass
class Column:
    id: str
    title: str  # unique within the board (duplicates get the column id appended)
    type: str


@dataclass
class BoardData:
    board_id: str
    name: str
    columns: List[Column]
    rows: List[Dict[str, Any]]  # keys: item_id, item_name, group + one key per column title
    truncated: bool = False  # True if we stopped before the last page

    def to_dataframe(self):
        import pandas as pd

        cols = ["item_id", "item_name", "group"] + [c.title for c in self.columns]
        return pd.DataFrame(self.rows, columns=cols)

    @property
    def column_types(self) -> Dict[str, str]:
        return {c.title: c.type for c in self.columns}


# ------------------------------------------------------------------------- queries
_ITEM_FIELDS = """
  id
  name
  group { id title }
  column_values { id type text }
"""

_LIST_BOARDS = """
query ($limit: Int!, $page: Int!) {
  boards(limit: $limit, page: $page, state: active) { id name }
}
"""

_FIRST_PAGE = (
    """
query ($boardId: ID!, $limit: Int!) {
  boards(ids: [$boardId]) {
    id
    name
    columns { id title type }
    items_page(limit: $limit) {
      cursor
      items { %s }
    }
  }
}
"""
    % _ITEM_FIELDS
)

_NEXT_PAGE = (
    """
query ($cursor: String!, $limit: Int!) {
  next_items_page(limit: $limit, cursor: $cursor) {
    cursor
    items { %s }
  }
}
"""
    % _ITEM_FIELDS
)

_VERSION = "query { version { kind value } }"


# -------------------------------------------------------------------------- client
def _raise_for_body_errors(body: Dict[str, Any]) -> None:
    errors = body.get("errors") or []
    top_code = body.get("error_code")
    if not errors and not top_code:
        return
    codes = {top_code} | {(e.get("extensions") or {}).get("code") for e in errors if isinstance(e, dict)}
    codes.discard(None)
    messages = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict))
    messages = messages or str(body.get("error_message", "unknown error"))
    if codes & _AUTH_CODES:
        raise MondayAuthError("monday.com rejected the API token (unauthorized). Check MONDAY_API_TOKEN.")
    if codes & _NOT_FOUND_CODES:
        raise MondayNotFoundError(f"monday.com could not find that board or item: {messages}")
    if codes & _RATE_CODES:
        raise MondayRateLimitError("monday.com rate/complexity limit reached.")
    raise MondayError(f"monday.com returned an error: {messages}")


def _parse_retry_after(resp: Any) -> Optional[float]:
    try:
        return float(resp.headers.get("Retry-After"))
    except (TypeError, ValueError, AttributeError):
        return None


class MondayClient:
    def __init__(
        self,
        token: Optional[str],
        api_version: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        session: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not token:
            raise MondayAuthError("MONDAY_API_TOKEN is not set.")
        self._headers = {"Authorization": token, "Content-Type": "application/json"}
        if api_version:  # unset = monday's current version
            self._headers["API-Version"] = api_version
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._session = session or requests.Session()
        self._sleep = sleep

    # ---- low level -------------------------------------------------------------
    def execute(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_read_only(query)
        last_error: Optional[MondayRetryableError] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._post_once(query, variables or {})
            except MondayRetryableError as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                wait = exc.retry_after if exc.retry_after is not None else min(2**attempt, 20)
                logger.warning("monday.com call failed (%s); retry %d in %.0fs", exc, attempt, wait)
                self._sleep(min(wait, 30))
        assert last_error is not None
        raise MondayError(f"{last_error} Please try again in a minute.") from last_error

    def _post_once(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self._session.post(
                API_URL,
                json={"query": query, "variables": variables},
                headers=self._headers,
                timeout=self._timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise MondayTransientError("Could not reach monday.com (network problem or timeout).") from exc

        if resp.status_code in (401, 403):
            raise MondayAuthError(
                "monday.com rejected the API token (HTTP %d). Check MONDAY_API_TOKEN and that the "
                "token's user is a member or admin, not a viewer." % resp.status_code
            )
        if resp.status_code == 429:
            raise MondayRateLimitError("monday.com rate limit hit.", retry_after=_parse_retry_after(resp))
        if resp.status_code >= 500:
            raise MondayTransientError(f"monday.com server error (HTTP {resp.status_code}).")
        if resp.status_code != 200:
            raise MondayError(f"Unexpected response from monday.com (HTTP {resp.status_code}).")
        try:
            body = resp.json()
        except ValueError as exc:
            raise MondayError("monday.com returned a response that is not valid JSON.") from exc
        _raise_for_body_errors(body)
        return body.get("data") or {}

    # ---- high level ------------------------------------------------------------
    def api_version(self) -> str:
        data = self.execute(_VERSION)
        v = data.get("version") or {}
        return f"{v.get('value', '?')} ({v.get('kind', '?')})"

    def list_boards(self, limit: int = 100, max_pages: int = 10) -> List[Dict[str, str]]:
        boards: List[Dict[str, str]] = []
        for page in range(1, max_pages + 1):
            data = self.execute(_LIST_BOARDS, {"limit": limit, "page": page})
            batch = data.get("boards") or []
            boards.extend({"id": str(b["id"]), "name": str(b["name"])} for b in batch)
            if len(batch) < limit:
                break
        return boards

    def fetch_board(
        self, board_id: str, page_size: int = DEFAULT_PAGE_SIZE, max_pages: int = MAX_PAGES
    ) -> BoardData:
        data = self.execute(_FIRST_PAGE, {"boardId": str(board_id), "limit": page_size})
        boards = data.get("boards") or []
        if not boards:
            raise MondayNotFoundError(
                f"Board {board_id} was not found. Check the board ID and that the token's user can see it."
            )
        board = boards[0]
        columns = _unique_columns(board.get("columns") or [])
        page = board.get("items_page") or {}
        raw_items: List[Dict[str, Any]] = list(page.get("items") or [])
        cursor = page.get("cursor")
        pages = 1
        while cursor and pages < max_pages:
            data = self.execute(_NEXT_PAGE, {"cursor": cursor, "limit": page_size})
            page = data.get("next_items_page") or {}
            raw_items.extend(page.get("items") or [])
            cursor = page.get("cursor")
            pages += 1
        return BoardData(
            board_id=str(board["id"]),
            name=str(board["name"]),
            columns=columns,
            rows=[_flatten_item(item, columns) for item in raw_items],
            truncated=bool(cursor),
        )


def _unique_columns(raw_columns: List[Dict[str, Any]]) -> List[Column]:
    seen: Dict[str, int] = {}
    for c in raw_columns:
        seen[c["title"]] = seen.get(c["title"], 0) + 1
    cols = []
    for c in raw_columns:
        title = c["title"] if seen[c["title"]] == 1 else f"{c['title']} ({c['id']})"
        cols.append(Column(id=c["id"], title=title, type=c.get("type", "")))
    return cols


def _flatten_item(item: Dict[str, Any], columns: List[Column]) -> Dict[str, Any]:
    by_id = {cv["id"]: cv for cv in item.get("column_values") or []}
    row: Dict[str, Any] = {
        "item_id": str(item.get("id", "")),
        "item_name": item.get("name") or "",
        "group": (item.get("group") or {}).get("title", ""),
    }
    for col in columns:
        text = (by_id.get(col.id) or {}).get("text")
        row[col.title] = text if text is not None else ""
    return row
