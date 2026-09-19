"""Cleaning layer for the two monday.com boards.

Design notes (Decision Log material):
  * We NEVER silently drop rows that carry real business data. We drop only rows that
    are structurally not data (leaked header rows, exact duplicates, blank names).
  * Every other issue (missing values, status/stage mismatches, negative amounts,
    mixed units, stale dates) is kept in the data and surfaced as a caveat string,
    so the agent can mention it instead of pretending the number is clean.
  * Each clean_* function returns (dataframe, DataQualityReport). The report is a
    list of short human-readable notes plus a few structured counts the analytics
    layer can reuse (e.g. how many Won deals have no value).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

# --------------------------------------------------------------------------- shared

@dataclass
class DataQualityReport:
    board: str
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)

    def add(self, note: str) -> None:
        self.notes.append(note)


def _to_numeric(series: pd.Series) -> pd.Series:
    """monday.com numeric columns arrive as text; coerce cleanly, keep NaN for junk."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip().replace({"": None, "nan": None}),
        errors="coerce",
    )


def _clean_str(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().replace({"nan": None, "": None, "None": None})


# ----------------------------------------------------------------- quantity parsing

# canonicalises typos / variants seen in the real data: "Acr", "Acers" -> "Acres", etc.
_UNIT_ALIASES = {
    "HA": "Hectares", "HECTARES": "Hectares",
    "ACR": "Acres", "ACRES": "Acres", "ACERS": "Acres",
    "KM": "Km", "RKM": "Route-Km",
    "DAYS": "Days", "MONTHS": "Months",
    "TOWERS": "Towers", "PILLARS": "Pillars", "MINES": "Mines",
    "MW": "MW", "SITES": "Sites", "ROOFTOPS": "Rooftops",
    "SLABS": "Slabs", "SUBSCRIPTIONS": "Subscriptions", "UNITS": "Units",
    "IMAGES": "Images", "LOCATION": "Locations", "AU": "AU", "S": "Units",
}

_QTY_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z]+)?\s*$")


def parse_quantity(raw) -> Optional[Dict[str, object]]:
    """'5360 HA' -> {'value': 5360.0, 'unit': 'Hectares'}. Unit-less numbers get unit=None
    (they are NOT assumed to be any particular unit -- that would be inventing data)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    m = _QTY_RE.match(s)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    unit_raw = (m.group(2) or "").upper()
    unit = _UNIT_ALIASES.get(unit_raw, unit_raw.title() if unit_raw else None)
    return {"value": value, "unit": unit}


def _quantity_columns(df: pd.DataFrame, base_col: str) -> pd.DataFrame:
    parsed = df[base_col].apply(parse_quantity)
    return pd.DataFrame(
        {
            f"{base_col} :: value": parsed.apply(lambda d: d["value"] if d else None),
            f"{base_col} :: unit": parsed.apply(lambda d: d["unit"] if d else None),
        }
    )


# ------------------------------------------------------------------- Work Orders

_WO_QTY_COLS = ["Quantities as per PO", "Quantity by Ops", "Quantity billed (till date)", "Balance in quantity"]


def clean_work_orders(df: pd.DataFrame) -> tuple[pd.DataFrame, DataQualityReport]:
    report = DataQualityReport(board="Work Orders")
    df = df.copy()
    report.stats["rows_in"] = len(df)

    # drop columns that are entirely empty on THIS pull, whatever they're called --
    # covers the 4 known-empty WO columns and monday's own always-blank primary
    # "Name" column (present live, absent from the raw spreadsheet export).
    protected = {"item_id", "item_name", "group"}
    empty_now = [
        c for c in df.columns
        if c not in protected and df[c].astype(str).str.strip().replace("nan", "").eq("").all()
    ]
    if empty_now:
        df = df.drop(columns=empty_now)
        report.add(f"Dropped {len(empty_now)} columns that are 100% empty on this pull: {', '.join(empty_now)}.")

    # 2. normalise identifying / categorical text
    for col in ["Deal name masked", "Sector", "Type of Work", "Execution Status", "Invoice Status",
                "Billing Status", "WO Status (billed)", "Document Type"]:
        if col in df.columns:
            df[col] = _clean_str(df[col])

    # fix known case-typos, e.g. "BIlled" -> "Billed"
    if "Billing Status" in df.columns:
        n_typo = df["Billing Status"].astype(str).str.fullmatch("BIlled", case=False, na=False).sum()
        df["Billing Status"] = df["Billing Status"].apply(
            lambda v: "Billed" if isinstance(v, str) and v.strip().lower() == "billed" else v
        )
        if n_typo:
            report.add(f"Normalised {n_typo} rows with the 'BIlled' typo in Billing Status to 'Billed'.")

    # normalise month text (e.g. "Dec" -> "December", "June" already fine)
    if "Actual Billing Month" in df.columns:
        _MONTHS = {m[:3].lower(): m for m in [
            "January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"]}
        df["Actual Billing Month"] = df["Actual Billing Month"].apply(
            lambda v: _MONTHS.get(str(v).strip()[:3].lower(), v) if pd.notna(v) else v
        )

    # 3. money columns: force numeric, keep sign (negatives are real credit-note style
    # adjustments here, not typos -- we flag them rather than clip to zero or drop them)
    money_cols = [c for c in df.columns if "Rupees" in c or "Amount to be billed" in c or "Amount Receivable" in c]
    for col in money_cols:
        df[col] = _to_numeric(df[col])

    for col in ["Amount to be billed in Rs. (Exl. of GST) (Masked)", "Amount Receivable (Masked)"]:
        if col in df.columns:
            n_neg = int((df[col] < 0).sum())
            if n_neg:
                report.add(f"{n_neg} rows have a negative value in '{col}' (likely credit/adjustment entries) — kept, not zeroed.")
                report.stats[f"negative_{col}"] = n_neg

    # 4. quantities: mixed units, can't be summed together -- parse into (value, unit) pairs
    # instead of one blended number, so analytics can report totals per unit honestly.
    for col in _WO_QTY_COLS:
        if col in df.columns:
            parsed = _quantity_columns(df, col)
            df = pd.concat([df, parsed], axis=1)
    report.add(
        "Quantity columns use mixed, non-convertible units (Hectares, Acres, Km, Days, Towers, "
        "MW, Sites, ...). Each is parsed into a numeric value + unit; totals are reported per unit, "
        "never summed across units."
    )

    report.stats["rows_out"] = len(df)
    return df, report


# ------------------------------------------------------------------------- Deals

_HEADER_LEAK_COLS = ["Deal Status", "Sector/service"]


def _drop_leaked_header_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Some source rows are literally a repeated header (e.g. a cell containing the
    text 'Deal Status'). A row counts as a leaked header if 2+ of its cells equal
    their own column name."""
    match_count = sum((df[c].astype(str).str.strip() == c) for c in _HEADER_LEAK_COLS if c in df.columns)
    mask = match_count >= 1  # even one is enough for our two marker columns
    return df[~mask].copy(), int(mask.sum())


def clean_deals(df: pd.DataFrame) -> tuple[pd.DataFrame, DataQualityReport]:
    report = DataQualityReport(board="Deals")
    df = df.copy()
    report.stats["rows_in"] = len(df)

    protected = {"item_id", "item_name", "group"}
    empty_now = [
        c for c in df.columns
        if c not in protected and df[c].astype(str).str.strip().replace("nan", "").eq("").all()
    ]
    if empty_now:
        df = df.drop(columns=empty_now)
        report.add(f"Dropped {len(empty_now)} columns that are 100% empty on this pull: {', '.join(empty_now)}.")

    # On the live board there is no "Deal Name" text column -- the masked deal name
    # lives in the item's own name field (already surfaced as "item_name" by
    # BoardData.to_dataframe()). We standardise on a "Deal Name" column either way so
    # the rest of this module (and analytics.py) can rely on it existing.
    if "Deal Name" not in df.columns and "item_name" in df.columns:
        df["Deal Name"] = df["item_name"]
        report.add("No 'Deal Name' column on the live board — using the item's name field as the deal name instead.")

    df, n_header = _drop_leaked_header_rows(df)
    if n_header:
        report.add(f"Dropped {n_header} rows that were repeated header rows leaked into the data.")

    # exact-duplicate check ignores item_id/group: on the live board every item has a
    # unique monday.com item_id even when it's a duplicate entry of the same deal, so
    # dedup must be on the business fields, not the id.
    business_cols = [c for c in df.columns if c not in ("item_id", "item_name", "group")]
    n_dupe = int(df.duplicated(subset=business_cols).sum())
    if n_dupe:
        df = df.drop_duplicates(subset=business_cols)
        report.add(f"Dropped {n_dupe} exact duplicate rows.")

    for col in ["Deal Name", "Deal Status", "Deal Stage", "Sector/service", "Product deal"]:
        if col in df.columns:
            df[col] = _clean_str(df[col])

    n_no_name = int(df["Deal Name"].isna().sum()) if "Deal Name" in df.columns else 0
    if n_no_name:
        df = df[df["Deal Name"].notna()]
        report.add(f"Dropped {n_no_name} rows with no deal name (nothing to key them on).")

    if "Masked Deal value" in df.columns:
        df["Masked Deal value"] = _to_numeric(df["Masked Deal value"])
        n_won = int((df["Deal Status"] == "Won").sum()) if "Deal Status" in df.columns else None
        n_won_no_value = int(((df["Deal Status"] == "Won") & df["Masked Deal value"].isna()).sum()) if "Deal Status" in df.columns else None
        n_missing_value = int(df["Masked Deal value"].isna().sum())
        report.stats["deal_value_missing"] = n_missing_value
        report.stats["won_missing_value"] = n_won_no_value
        report.add(
            f"Deal value is missing on {n_missing_value}/{len(df)} deals, including "
            f"{n_won_no_value}/{n_won} 'Won' deals — revenue totals below only cover deals with a known value."
        )

    # The stage taxonomy is an ordered funnel (A. Lead Generated ... G. Project Won ...).
    # A "Won" deal whose stage is still early in that funnel (before the stage that
    # actually means won) is the Status/Stage disagreement called out in the brief.
    _EARLY_STAGES = {"A. Lead Generated", "B. Sales Qualified Leads", "C. Demo Done",
                      "D. Feasibility", "E. Proposal/Commercials Sent", "F. Negotiations"}
    if "Deal Status" in df.columns and "Deal Stage" in df.columns:
        mismatch = df["Deal Status"].eq("Won") & df["Deal Stage"].isin(_EARLY_STAGES)
        n_mismatch = int(mismatch.sum())
        report.stats["won_stage_mismatch"] = n_mismatch
        if n_mismatch:
            report.add(
                f"{n_mismatch} deals are marked Status='Won' but Stage is still an early funnel stage "
                "(e.g. 'A. Lead Generated') — Stage was likely never updated after the deal closed. "
                "We treat 'Deal Status' as the source of truth for won/lost/open (it is the explicit "
                "outcome field); 'Deal Stage' is used only to describe where OPEN deals sit in the funnel."
            )

    if "Masked Deal value" in df.columns:
        q3 = df["Masked Deal value"].quantile(0.75)
        iqr = df["Masked Deal value"].quantile(0.75) - df["Masked Deal value"].quantile(0.25)
        outlier_cut = q3 + 3 * iqr if pd.notna(iqr) else None
        outliers = df[df["Masked Deal value"] > outlier_cut] if outlier_cut else df.iloc[0:0]
        df["is_value_outlier"] = df["Masked Deal value"] > outlier_cut if outlier_cut else False
        if len(outliers):
            names = ", ".join(outliers["Deal Name"].head(3).tolist())
            report.add(
                f"{len(outliers)} deal(s) are extreme value outliers (e.g. {names}) that can dominate a sum; "
                "pipeline/revenue tools report totals both with and without outliers."
            )

    if "Tentative Close Date" in df.columns:
        tcd = pd.to_datetime(df["Tentative Close Date"], errors="coerce")
        today = pd.Timestamp.now().normalize()
        is_open = df["Deal Status"].eq("Open") if "Deal Status" in df.columns else pd.Series(False, index=df.index)
        overdue = is_open & tcd.notna() & (tcd < today)
        df["tentative_close_overdue"] = overdue
        n_overdue = int(overdue.sum())
        n_open = int(is_open.sum())
        if n_open:
            report.add(
                f"{n_overdue}/{n_open} open deals have a tentative close date already in the past. "
                "\"This quarter\" pipeline is therefore reported as the current Open-deal snapshot "
                "(by Deal Status), not filtered by tentative close date; stale dates are flagged separately."
            )

    report.stats["rows_out"] = len(df)
    return df, report