"""Analytics tools.

Every function takes the two CLEANED dataframes (or is given them via the module-level
`set_data`) and returns a plain dict: {"data": ..., "caveats": [...]}.  The agent hands
this dict straight to the LLM as a tool result — the LLM explains it, it never computes
the numbers itself.  Keeping "caveats" as a first-class field means data-quality notes
travel with every answer instead of living only in a README nobody reads mid-chat.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

_STATE: Dict[str, object] = {}


def set_data(work_orders: pd.DataFrame, deals: pd.DataFrame,
             wo_caveats: List[str], deal_caveats: List[str]) -> None:
    _STATE["wo"] = work_orders
    _STATE["deals"] = deals
    _STATE["wo_caveats"] = wo_caveats
    _STATE["deal_caveats"] = deal_caveats


def _deals() -> pd.DataFrame:
    return _STATE["deals"]


def _wo() -> pd.DataFrame:
    return _STATE["wo"]


def _round(x) -> Optional[float]:
    return None if x is None or pd.isna(x) else round(float(x), 2)


# ------------------------------------------------------------------------- pipeline

def pipeline_summary(sector: Optional[str] = None) -> dict:
    """Open-deal pipeline value: total, by sector, with and without value outliers.
    `sector` optionally filters to one Sector/service value (case-insensitive)."""
    d = _deals()
    open_deals = d[d["Deal Status"] == "Open"].copy()
    if sector:
        open_deals = open_deals[open_deals["Sector/service"].str.lower() == sector.lower()]

    known = open_deals[open_deals["Masked Deal value"].notna()]
    no_outliers = known[~known.get("is_value_outlier", False)]

    by_sector = (
        known.groupby("Sector/service")["Masked Deal value"].agg(["sum", "count"]).round(2)
        .rename(columns={"sum": "value", "count": "deal_count"}).reset_index()
        .sort_values("value", ascending=False).to_dict("records")
    )

    return {
        "data": {
            "open_deal_count": int(len(open_deals)),
            "open_deals_with_known_value": int(len(known)),
            "total_pipeline_value_incl_outliers": _round(known["Masked Deal value"].sum()),
            "total_pipeline_value_excl_outliers": _round(no_outliers["Masked Deal value"].sum()),
            "outlier_deal_count": int(known.get("is_value_outlier", pd.Series(dtype=bool)).sum()),
            "by_sector": by_sector,
        },
        "caveats": [
            f"{len(open_deals) - len(known)}/{len(open_deals)} open deals have no recorded value and are excluded from these totals.",
            "48/49 open deals have a tentative close date already in the past, so this is a current snapshot by status, not a date-filtered 'this quarter' number.",
            "No shared ID exists between boards, so this pipeline cannot be reconciled item-for-item against Work Orders.",
        ],
    }


def revenue_summary() -> dict:
    """Realised revenue: Won deals' value, with and without outliers, plus coverage."""
    d = _deals()
    won = d[d["Deal Status"] == "Won"]
    known = won[won["Masked Deal value"].notna()]
    no_outliers = known[~known.get("is_value_outlier", False)]
    stage_mismatch = int((won["Deal Stage"].isin(
        {"A. Lead Generated", "B. Sales Qualified Leads", "C. Demo Done",
         "D. Feasibility", "E. Proposal/Commercials Sent", "F. Negotiations"})).sum())

    return {
        "data": {
            "won_deal_count": int(len(won)),
            "won_with_known_value": int(len(known)),
            "total_won_value_incl_outliers": _round(known["Masked Deal value"].sum()),
            "total_won_value_excl_outliers": _round(no_outliers["Masked Deal value"].sum()),
        },
        "caveats": [
            f"Only {len(known)}/{len(won)} 'Won' deals have a recorded value — this total is a floor, not the true realised revenue.",
            f"{stage_mismatch} 'Won' deals still show an early funnel stage (e.g. Lead Generated), suggesting Stage wasn't updated after close; Status was used as the source of truth.",
        ],
    }


def sector_breakdown() -> dict:
    """Deal counts/value by sector, alongside Work Order counts by sector — an
    approximate cross-board view since there is no shared ID."""
    d, w = _deals(), _wo()
    deal_side = (
        d.groupby("Sector/service").agg(deal_count=("Deal Name", "count"),
                                         known_value_sum=("Masked Deal value", "sum")).round(2)
    )
    wo_side = w.groupby("Sector").size().rename("work_order_count")
    combined = deal_side.join(wo_side, how="outer").fillna(0).reset_index().rename(columns={"index": "sector"})
    return {
        "data": combined.to_dict("records"),
        "caveats": [
            "Sector naming isn't identical across boards in every case; this join is by sector label only, not by deal identity.",
            "52 deal names overlap (by masked name) between the two boards out of 346 deals / 176 work orders — most rows cannot be matched 1:1.",
        ],
    }


def billing_status_summary() -> dict:
    """Work Order billing/collection state: amounts to be billed, receivable, and
    counts by invoice/WO status."""
    w = _wo()
    to_be_billed = w.get("Amount to be billed in Rs. (Exl. of GST) (Masked)", pd.Series(dtype=float))
    receivable = w.get("Amount Receivable (Masked)", pd.Series(dtype=float))
    by_invoice_status = w["Invoice Status"].value_counts(dropna=True).to_dict() if "Invoice Status" in w.columns else {}
    by_wo_status = w["WO Status (billed)"].value_counts(dropna=True).to_dict() if "WO Status (billed)" in w.columns else {}

    return {
        "data": {
            "total_amount_to_be_billed": _round(to_be_billed.sum()),
            "total_amount_receivable": _round(receivable.sum()),
            "negative_to_be_billed_rows": int((to_be_billed < 0).sum()),
            "negative_receivable_rows": int((receivable < 0).sum()),
            "by_invoice_status": by_invoice_status,
            "by_wo_status": by_wo_status,
        },
        "caveats": [
            "6 rows have a negative 'amount to be billed' and 11 a negative receivable (likely credit/adjustment entries); they are included as-is, not zeroed out, so they slightly reduce these totals.",
            "'Invoice Status' and 'WO Status (billed)' are each blank on roughly a third to half of rows.",
        ],
    }


def quantity_summary(type_of_work: Optional[str] = None) -> dict:
    """Total surveyed quantity BY UNIT (Hectares, Acres, Km, ...). Units are never
    summed together since they measure different things."""
    w = _wo()
    if type_of_work:
        w = w[w["Type of Work"].str.contains(type_of_work, case=False, na=False)]
    val_col, unit_col = "Quantities as per PO :: value", "Quantities as per PO :: unit"
    if val_col not in w.columns:
        return {"data": {}, "caveats": ["Quantity columns were not found — was clean_work_orders() run?"]}
    by_unit = (
        w.dropna(subset=[val_col]).groupby(unit_col)[val_col].agg(["sum", "count"]).round(2)
        .rename(columns={"sum": "total", "count": "work_order_count"}).reset_index()
        .rename(columns={unit_col: "unit"}).to_dict("records")
    )
    return {
        "data": {"by_unit": by_unit},
        "caveats": [
            "Roughly half of quantity values have no unit at all in the source data; those rows are excluded here rather than guessed at.",
        ],
    }


def data_quality_overview() -> dict:
    """All data-quality caveats gathered during cleaning, for when the founder asks
    'how reliable is this data' directly."""
    return {
        "data": {
            "work_orders": {"caveats": _STATE.get("wo_caveats", [])},
            "deals": {"caveats": _STATE.get("deal_caveats", [])},
        },
        "caveats": [],
    }


TOOLS = {
    "pipeline_summary": pipeline_summary,
    "revenue_summary": revenue_summary,
    "sector_breakdown": sector_breakdown,
    "billing_status_summary": billing_status_summary,
    "quantity_summary": quantity_summary,
    "data_quality_overview": data_quality_overview,
}