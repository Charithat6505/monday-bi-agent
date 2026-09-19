import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from bi_agent import data_source  # noqa: E402
from bi_agent.config import Settings  # noqa: E402
from bi_agent.monday_client import (  # noqa: E402
    BoardData,
    MondayAuthError,
    MondayClient,
    MondayError,
    MondayNotFoundError,
    ReadOnlyViolation,
    assert_read_only,
)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


COLUMNS = [
    {"id": "status", "title": "Status", "type": "status"},
    {"id": "amt", "title": "Amount", "type": "numbers"},
]


def item(i, name, status="", amt=""):
    return {
        "id": str(i),
        "name": name,
        "group": {"id": "g", "title": "Group 1"},
        "column_values": [
            {"id": "status", "type": "status", "text": status},
            {"id": "amt", "type": "numbers", "text": amt},
        ],
    }


def first_page(items, cursor, columns=COLUMNS, name="Deals"):
    return FakeResponse(
        body={"data": {"boards": [{"id": "1", "name": name, "columns": columns,
                                   "items_page": {"cursor": cursor, "items": items}}]}}
    )


def client_with(responses, **kw):
    session = FakeSession(responses)
    return MondayClient("tok", session=session, sleep=lambda s: None, **kw), session


class ReadOnlyGuardTests(unittest.TestCase):
    def test_allows_queries(self):
        assert_read_only("query { boards { id } }")
        assert_read_only("{ boards { id } }")
        assert_read_only("# comment\nquery ($a: ID!) { boards(ids: [$a]) { id } }")

    def test_blocks_writes(self):
        for q in ["mutation { create_item(board_id: 1, item_name: \"x\") { id } }",
                  "  MUTATION { delete_item(item_id: 1) { id } }",
                  "subscription { x }",
                  "query { boards { id } } mutation { delete_item(item_id: 1) { id } }"]:
            with self.assertRaises(ReadOnlyViolation):
                assert_read_only(q)

    def test_execute_refuses_mutation_without_calling_api(self):
        client, session = client_with([])
        with self.assertRaises(ReadOnlyViolation):
            client.execute("mutation { delete_item(item_id: 1) { id } }")
        self.assertEqual(session.calls, [])


class FetchBoardTests(unittest.TestCase):
    def test_pagination_follows_cursor(self):
        client, session = client_with([
            first_page([item(1, "A"), item(2, "B")], cursor="abc"),
            FakeResponse(body={"data": {"next_items_page": {"cursor": None, "items": [item(3, "C", "Won", "100")]}}}),
        ])
        board = client.fetch_board("1", page_size=2)
        self.assertEqual([r["item_name"] for r in board.rows], ["A", "B", "C"])
        self.assertEqual(len(session.calls), 2)
        self.assertIn("next_items_page", session.calls[1]["json"]["query"])
        self.assertEqual(session.calls[1]["json"]["variables"]["cursor"], "abc")
        self.assertFalse(board.truncated)
        self.assertEqual(board.rows[2]["Status"], "Won")
        self.assertEqual(board.rows[2]["Amount"], "100")

    def test_null_text_becomes_empty_string_and_dataframe_shape(self):
        client, _ = client_with([first_page([{"id": "1", "name": "A", "group": None,
                                              "column_values": [{"id": "amt", "type": "numbers", "text": None}]}], None)])
        board = client.fetch_board("1")
        df = board.to_dataframe()
        self.assertEqual(list(df.columns), ["item_id", "item_name", "group", "Status", "Amount"])
        self.assertEqual(df.loc[0, "Amount"], "")
        self.assertEqual(df.loc[0, "Status"], "")

    def test_duplicate_column_titles_are_made_unique(self):
        cols = [{"id": "t1", "title": "Notes", "type": "text"}, {"id": "t2", "title": "Notes", "type": "text"}]
        client, _ = client_with([first_page([], None, columns=cols)])
        board = client.fetch_board("1")
        self.assertEqual([c.title for c in board.columns], ["Notes (t1)", "Notes (t2)"])

    def test_truncation_flag_when_max_pages_hit(self):
        client, _ = client_with([first_page([item(1, "A")], cursor="abc")])
        board = client.fetch_board("1", max_pages=1)
        self.assertTrue(board.truncated)

    def test_missing_board_raises_not_found(self):
        client, _ = client_with([FakeResponse(body={"data": {"boards": []}})])
        with self.assertRaises(MondayNotFoundError):
            client.fetch_board("999")

    def test_api_version_header_only_when_configured(self):
        client, session = client_with([FakeResponse(body={"data": {"boards": []}})])
        client.execute("query { boards { id } }")
        self.assertNotIn("API-Version", session.calls[0]["headers"])
        client2, session2 = client_with([FakeResponse(body={"data": {"boards": []}})], api_version="2026-01")
        client2.execute("query { boards { id } }")
        self.assertEqual(session2.calls[0]["headers"]["API-Version"], "2026-01")


class ErrorHandlingTests(unittest.TestCase):
    def test_auth_error_not_retried(self):
        client, session = client_with([FakeResponse(status=401)])
        with self.assertRaises(MondayAuthError):
            client.execute("query { boards { id } }")
        self.assertEqual(len(session.calls), 1)

    def test_graphql_unauthorized_code_maps_to_auth_error(self):
        body = {"errors": [{"message": "no", "extensions": {"code": "UserUnauthorizedException"}}]}
        client, _ = client_with([FakeResponse(body=body)])
        with self.assertRaises(MondayAuthError):
            client.execute("query { boards { id } }")

    def test_rate_limit_then_success_is_retried(self):
        client, session = client_with([
            FakeResponse(status=429, headers={"Retry-After": "1"}),
            FakeResponse(body={"data": {"boards": [{"id": "1"}]}}),
        ])
        data = client.execute("query { boards { id } }")
        self.assertEqual(data["boards"][0]["id"], "1")
        self.assertEqual(len(session.calls), 2)

    def test_complexity_error_in_200_body_is_retried(self):
        body = {"errors": [{"message": "too complex", "extensions": {"code": "ComplexityException"}}]}
        client, session = client_with([FakeResponse(body=body), FakeResponse(body={"data": {"ok": 1}})])
        self.assertEqual(client.execute("query { x }")["ok"], 1)
        self.assertEqual(len(session.calls), 2)

    def test_timeout_then_success(self):
        client, session = client_with([requests.Timeout(), FakeResponse(body={"data": {"ok": 1}})])
        self.assertEqual(client.execute("query { x }")["ok"], 1)

    def test_gives_up_after_max_retries_with_friendly_message(self):
        client, session = client_with([FakeResponse(status=503)] * 3)
        with self.assertRaises(MondayError) as ctx:
            client.execute("query { x }")
        self.assertIn("try again", str(ctx.exception).lower())
        self.assertEqual(len(session.calls), 3)

    def test_missing_token(self):
        with self.assertRaises(MondayAuthError):
            MondayClient(None)


class FakeDataClient:
    def __init__(self):
        self.fail = False
        self.fetches = 0

    def list_boards(self):
        return [
            {"id": "33", "name": "Subitems of Work Orders"},
            {"id": "11", "name": "Work Orders"},
            {"id": "22", "name": "Deals"},
        ]

    def fetch_board(self, board_id):
        self.fetches += 1
        if self.fail:
            raise MondayError("monday.com is down")
        return BoardData(board_id=board_id, name="x", columns=[], rows=[{"item_id": "1"}])


class DataSourceTests(unittest.TestCase):
    def setUp(self):
        data_source.clear_cache()
        self.settings = Settings(monday_token="t", cache_ttl_seconds=300)

    def test_boards_found_by_name_ignoring_subitem_boards(self):
        c = FakeDataClient()
        self.assertEqual(data_source.resolve_board_id(c, self.settings, "work_orders"), "11")
        self.assertEqual(data_source.resolve_board_id(c, self.settings, "deals"), "22")

    def test_board_id_override(self):
        s = Settings(monday_token="t", deals_board_id="555")
        self.assertEqual(data_source.resolve_board_id(FakeDataClient(), s, "deals"), "555")

    def test_missing_board_gives_helpful_error(self):
        class Empty(FakeDataClient):
            def list_boards(self):
                return [{"id": "1", "name": "Marketing"}]
        with self.assertRaises(MondayNotFoundError) as ctx:
            data_source.resolve_board_id(Empty(), self.settings, "deals")
        self.assertIn("Marketing", str(ctx.exception))

    def test_cache_hit_avoids_second_fetch(self):
        c = FakeDataClient()
        data_source.load_board("deals", settings=self.settings, client=c)
        data_source.load_board("deals", settings=self.settings, client=c)
        self.assertEqual(c.fetches, 1)
        data_source.load_board("deals", force_refresh=True, settings=self.settings, client=c)
        self.assertEqual(c.fetches, 2)

    def test_stale_copy_served_when_monday_fails(self):
        c = FakeDataClient()
        data_source.load_board("deals", settings=self.settings, client=c)
        c.fail = True
        loaded = data_source.load_board("deals", force_refresh=True, settings=self.settings, client=c)
        self.assertTrue(loaded.stale)
        self.assertIn("earlier", loaded.warning)

    def test_failure_without_cache_raises(self):
        c = FakeDataClient()
        c.fail = True
        with self.assertRaises(MondayError):
            data_source.load_board("deals", settings=self.settings, client=c)


if __name__ == "__main__":
    unittest.main()
