"""Smoke test for the monday.com connection.

Run:  python check_monday.py

It prints the API version, the boards your token can see, and for the Work Orders and
Deals boards: item count, column ids/titles/types, % blank per column and 3 sample rows.
It also saves the raw data to ./debug/ (git-ignored) so we can design the cleaning layer
against exactly what monday.com returns.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bi_agent import data_source
from bi_agent.config import load_settings
from bi_agent.monday_client import MondayClient, MondayError

EXPECTED = {"work_orders": 176, "deals": 346}


def main() -> int:
    settings = load_settings()
    if settings.missing_for_monday():
        print("Missing:", ", ".join(settings.missing_for_monday()), "(put it in .env, see .env.example)")
        return 1

    try:
        client = MondayClient(settings.monday_token, settings.monday_api_version)
        print("monday API version in use:", client.api_version())
        print("\nBoards visible to this token:")
        for b in client.list_boards():
            print(f"  {b['id']:>12}  {b['name']}")

        out_dir = Path("debug")
        out_dir.mkdir(exist_ok=True)

        for kind in data_source.KINDS:
            loaded = data_source.load_board(kind, force_refresh=True, settings=settings, client=client)
            board = loaded.board
            df = board.to_dataframe()
            print("\n" + "=" * 78)
            print(f"{kind.upper()}  ->  board '{board.name}' (id {board.board_id})")
            print(f"items fetched: {len(df)}   (expected about {EXPECTED[kind]})   truncated: {board.truncated}")
            print(f"columns: {len(board.columns)}")
            print("\n  id | title | type | % blank")
            for col in board.columns:
                blank = (df[col.title].astype(str).str.strip() == "").mean() * 100
                print(f"  {col.id} | {col.title} | {col.type} | {blank:.0f}%")
            print("\nfirst 3 rows:")
            print(df.head(3).T.to_string())
            path = out_dir / f"raw_{kind}.csv"
            df.to_csv(path, index=False)
            print(f"\nsaved raw data -> {path}")
    except MondayError as exc:
        print("\nFAILED:", exc)
        return 1

    print("\nAll good. Send me the 'id | title | type | % blank' tables (or the two CSVs in debug/).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
