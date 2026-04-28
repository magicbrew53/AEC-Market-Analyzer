"""
compute.py — extract figures that drive both the comparison tables and the LLM prompts.

For each section (Total, Intl, or one of 9 sectors), we compute a structured "facts"
object that contains every number that could appear in either the data table or
the narrative prose. The LLM is told to ONLY cite numbers from this facts object —
that prevents it from inheriting any inconsistency in Don's prior narrative copy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd


def cagr(v_start: float, v_end: float, years: int) -> float:
    if v_start is None or v_end is None or v_start <= 0 or years <= 0:
        return None
    return (v_end / v_start) ** (1 / years) - 1


@dataclass
class YearRow:
    year: int
    rank: Optional[int]
    firm_nom_m: Optional[float]
    firm_real_m: Optional[float]
    comp_nom_m: Optional[float]
    comp_real_m: Optional[float]
    share: Optional[float]                # firm_nom_m / comp_nom_m
    yoy_premium: Optional[float]          # (firm_yoy_nom - comp_yoy_nom)
    is_forecast: bool = False


@dataclass
class SectionFacts:
    firm_short: str
    sector_label: str          # e.g. "Power", "Total Firm Revenue"
    sector_key: str            # e.g. "power", "total"
    base_year: int             # e.g. 2025

    # Endpoint values
    start_year: int
    end_year: int
    start_nom_m: float
    end_nom_m: float
    start_real_m: float
    end_real_m: float
    nominal_growth_pct: float
    nominal_cagr_pct: float
    real_cagr_pct: float

    # Composite over the same span
    comp_start_nom_m: float
    comp_end_nom_m: float
    comp_nominal_cagr_pct: float
    comp_real_cagr_pct: float

    # Outperformance
    cagr_premium_pct: float                # firm_nominal_cagr - comp_nominal_cagr

    # Market share trajectory
    start_share: float
    end_share: float
    share_change_bps: float
    best_share_year: Optional[int] = None
    best_share_value: Optional[float] = None
    worst_share_year: Optional[int] = None
    worst_share_value: Optional[float] = None

    # Sector contribution to firm total (sector sections only; None for total/intl)
    start_pct_of_firm_total: Optional[float] = None
    end_pct_of_firm_total: Optional[float] = None

    # Notable years
    largest_firm_yoy_real: Optional[tuple[int, float]] = None
    largest_firm_premium_year: Optional[tuple[int, float]] = None  # year, premium pct
    most_negative_firm_yoy_real: Optional[tuple[int, float]] = None

    # Recession test (2009)
    firm_2009_real_yoy: Optional[float] = None
    comp_2009_real_yoy: Optional[float] = None
    firm_2009_premium: Optional[float] = None

    # Rank progression
    start_rank: Optional[int] = None
    end_rank: Optional[int] = None
    best_rank: Optional[int] = None
    best_rank_year: Optional[int] = None

    # Year-by-year rows for the comparison table
    rows: list[YearRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # tuples → lists for JSON
        for k in ("largest_firm_yoy_real", "largest_firm_premium_year",
                  "most_negative_firm_yoy_real"):
            v = d.get(k)
            if v is not None:
                d[k] = list(v)
        return d


def _firm_col_for_sector(sector_key: str) -> str:
    if sector_key == "total":
        return "total_rev_m"
    if sector_key == "intl":
        return "intl_rev_m"
    return f"{sector_key}_m"


def _comp_col_for_sector(sector_key: str) -> str:
    return sector_key  # already named cleanly in composite_by_year


def compute_section_facts(
    firm_data: pd.DataFrame,
    composite_by_year: pd.DataFrame,
    sector_key: str,
    sector_label: str,
    firm_short: str,
    cci_lookup: dict,
    base_year: int = 2025,
    start_year: int = 2005,
    end_year: int = 2025,
    fmi_forecast=None,
) -> SectionFacts:
    """
    Build the facts object for one section.

    `firm_data` should be the firm's panel rows (output of get_firm_panel()).
    `composite_by_year` is the output of charts.build_composite_by_year(panel).
    `fmi_forecast` (optional): if provided, appends a forecast YearRow for the
       year (end_year + 1), with values projected by applying the FMI sector
       rate to the firm's end-year value (and the same rate to composite).
    """
    firm_col = _firm_col_for_sector(sector_key)
    comp_col = _comp_col_for_sector(sector_key)

    # Build the firm subset, including total_rev_m for pct_of_firm_total in sector
    # sections. When the sector itself IS total or intl, firm_col == "total_rev_m" or
    # "intl_rev_m" — handle the column-overlap carefully.
    firm_subset_cols = ["data_year", "rank"]
    if firm_col != "total_rev_m":
        firm_subset_cols.append("total_rev_m")
    firm_subset_cols.append(firm_col)
    firm_subset = firm_data[firm_subset_cols].rename(columns={firm_col: "firm_nom"})
    if firm_col == "total_rev_m":
        # When firm_col was total_rev_m, we renamed it to firm_nom; alias for downstream
        firm_subset["total_rev_m"] = firm_subset["firm_nom"]

    merged = pd.merge(
        firm_subset,
        composite_by_year[["data_year", comp_col]].rename(columns={comp_col: "comp_nom"}),
        on="data_year", how="outer",
    ).sort_values("data_year").reset_index(drop=True)

    # Add real columns
    merged["firm_real"] = merged.apply(
        lambda r: r["firm_nom"] * cci_lookup.get(int(r["data_year"]), 1.0)
        if pd.notna(r["firm_nom"]) else None, axis=1)
    merged["comp_real"] = merged.apply(
        lambda r: r["comp_nom"] * cci_lookup.get(int(r["data_year"]), 1.0)
        if pd.notna(r["comp_nom"]) else None, axis=1)

    merged["share"] = merged.apply(
        lambda r: (r["firm_nom"] / r["comp_nom"])
        if pd.notna(r["firm_nom"]) and pd.notna(r["comp_nom"]) and r["comp_nom"] > 0
        else None, axis=1)

    merged["firm_yoy_nom"] = merged["firm_nom"].pct_change()
    merged["comp_yoy_nom"] = merged["comp_nom"].pct_change()
    merged["firm_yoy_real"] = merged["firm_real"].pct_change()
    merged["comp_yoy_real"] = merged["comp_real"].pct_change()
    merged["yoy_premium"] = merged["firm_yoy_nom"] - merged["comp_yoy_nom"]

    if sector_key not in ("total", "intl"):
        merged["pct_of_firm_total"] = merged.apply(
            lambda r: (r["firm_nom"] / r["total_rev_m"])
            if pd.notna(r["firm_nom"]) and pd.notna(r["total_rev_m"]) and r["total_rev_m"] > 0
            else None, axis=1)
    else:
        merged["pct_of_firm_total"] = None

    # Filter to span
    span = merged[(merged["data_year"] >= start_year) & (merged["data_year"] <= end_year)].copy()
    if span.empty:
        raise ValueError(f"No data in span {start_year}-{end_year} for {sector_key}")

    # Endpoints
    start_row = span[span["data_year"] == start_year].iloc[0] if (span["data_year"] == start_year).any() \
        else span.iloc[0]
    end_row = span[span["data_year"] == end_year].iloc[0] if (span["data_year"] == end_year).any() \
        else span.iloc[-1]

    span_years = int(end_row["data_year"]) - int(start_row["data_year"])

    f_start_nom = float(start_row["firm_nom"]) if pd.notna(start_row["firm_nom"]) else None
    f_end_nom = float(end_row["firm_nom"]) if pd.notna(end_row["firm_nom"]) else None
    f_start_real = float(start_row["firm_real"]) if pd.notna(start_row["firm_real"]) else None
    f_end_real = float(end_row["firm_real"]) if pd.notna(end_row["firm_real"]) else None

    c_start_nom = float(start_row["comp_nom"]) if pd.notna(start_row["comp_nom"]) else None
    c_end_nom = float(end_row["comp_nom"]) if pd.notna(end_row["comp_nom"]) else None
    c_start_real = float(start_row["comp_real"]) if pd.notna(start_row["comp_real"]) else None
    c_end_real = float(end_row["comp_real"]) if pd.notna(end_row["comp_real"]) else None

    nominal_growth_pct = (f_end_nom / f_start_nom - 1) * 100 if f_start_nom and f_end_nom else None
    nominal_cagr_pct = cagr(f_start_nom, f_end_nom, span_years) * 100 if f_start_nom and f_end_nom else None
    real_cagr_pct = cagr(f_start_real, f_end_real, span_years) * 100 if f_start_real and f_end_real else None
    comp_nominal_cagr_pct = cagr(c_start_nom, c_end_nom, span_years) * 100 if c_start_nom and c_end_nom else None
    comp_real_cagr_pct = cagr(c_start_real, c_end_real, span_years) * 100 if c_start_real and c_end_real else None

    cagr_premium_pct = (nominal_cagr_pct - comp_nominal_cagr_pct) \
        if (nominal_cagr_pct is not None and comp_nominal_cagr_pct is not None) else None

    # Share metrics
    start_share = float(start_row["share"]) if pd.notna(start_row["share"]) else None
    end_share = float(end_row["share"]) if pd.notna(end_row["share"]) else None
    share_change_bps = (end_share - start_share) * 10000 if start_share and end_share else None

    valid_share = span.dropna(subset=["share"])
    best_share_year = best_share_value = None
    worst_share_year = worst_share_value = None
    if not valid_share.empty:
        bs = valid_share.loc[valid_share["share"].idxmax()]
        best_share_year = int(bs["data_year"])
        best_share_value = float(bs["share"])
        ws = valid_share.loc[valid_share["share"].idxmin()]
        worst_share_year = int(ws["data_year"])
        worst_share_value = float(ws["share"])

    # Sector contribution to firm total (sector sections only)
    start_pct_of_total = float(start_row["pct_of_firm_total"]) \
        if pd.notna(start_row.get("pct_of_firm_total")) else None
    end_pct_of_total = float(end_row["pct_of_firm_total"]) \
        if pd.notna(end_row.get("pct_of_firm_total")) else None

    # Notable years
    valid_yoy = span.dropna(subset=["firm_yoy_real"])
    largest_firm_yoy_real = None
    most_negative_firm_yoy_real = None
    if not valid_yoy.empty:
        r = valid_yoy.loc[valid_yoy["firm_yoy_real"].idxmax()]
        largest_firm_yoy_real = (int(r["data_year"]), float(r["firm_yoy_real"]))
        r = valid_yoy.loc[valid_yoy["firm_yoy_real"].idxmin()]
        most_negative_firm_yoy_real = (int(r["data_year"]), float(r["firm_yoy_real"]))

    valid_prem = span.dropna(subset=["yoy_premium"])
    largest_firm_premium_year = None
    if not valid_prem.empty:
        r = valid_prem.loc[valid_prem["yoy_premium"].idxmax()]
        largest_firm_premium_year = (int(r["data_year"]), float(r["yoy_premium"]))

    # 2009 recession test
    firm_2009_real_yoy = None
    comp_2009_real_yoy = None
    firm_2009_premium = None
    if (span["data_year"] == 2009).any():
        r2009 = span[span["data_year"] == 2009].iloc[0]
        if pd.notna(r2009["firm_yoy_real"]):
            firm_2009_real_yoy = float(r2009["firm_yoy_real"])
        if pd.notna(r2009["comp_yoy_real"]):
            comp_2009_real_yoy = float(r2009["comp_yoy_real"])
        if firm_2009_real_yoy is not None and comp_2009_real_yoy is not None:
            firm_2009_premium = firm_2009_real_yoy - comp_2009_real_yoy

    # Rank progression
    start_rank = int(start_row["rank"]) if pd.notna(start_row.get("rank")) else None
    end_rank = int(end_row["rank"]) if pd.notna(end_row.get("rank")) else None
    rank_valid = span.dropna(subset=["rank"])
    best_rank = best_rank_year = None
    if not rank_valid.empty:
        r = rank_valid.loc[rank_valid["rank"].idxmin()]  # min rank = best
        best_rank = int(r["rank"])
        best_rank_year = int(r["data_year"])

    # Per-year rows for the comparison table
    rows = []
    for _, r in span.iterrows():
        rows.append(YearRow(
            year=int(r["data_year"]),
            rank=int(r["rank"]) if pd.notna(r.get("rank")) else None,
            firm_nom_m=float(r["firm_nom"]) if pd.notna(r["firm_nom"]) else None,
            firm_real_m=float(r["firm_real"]) if pd.notna(r["firm_real"]) else None,
            comp_nom_m=float(r["comp_nom"]) if pd.notna(r["comp_nom"]) else None,
            comp_real_m=float(r["comp_real"]) if pd.notna(r["comp_real"]) else None,
            share=float(r["share"]) if pd.notna(r["share"]) else None,
            yoy_premium=float(r["yoy_premium"]) if pd.notna(r["yoy_premium"]) else None,
            is_forecast=False,
        ))

    # Append a forecast row if FMI rates are available.
    # Convention: forecast year = end_year + 1.
    # Apply the FMI sector rate to BOTH firm and composite, since FMI publishes
    # composite-level growth rates. For real values, hold deflator at base_year (1.0)
    # since we extrapolate one year out (this is approximate but conservative — Don's
    # report uses the same approach).
    if fmi_forecast is not None and fmi_forecast.rates:
        fcst_year = end_year + 1
        rate = fmi_forecast.rates.get(sector_key)
        if rate is not None and f_end_nom is not None:
            firm_fcst_nom = f_end_nom * (1 + rate)
            comp_fcst_nom = c_end_nom * (1 + rate) if c_end_nom else None
            # Real values one year out: apply same rate; deflator extrapolation is
            # negligible at 1-year horizon (CCI typically rises 1-3% annually, much
            # smaller than sector growth)
            firm_fcst_real = firm_fcst_nom  # treat as already in base-year $
            comp_fcst_real = comp_fcst_nom  # same
            fcst_share = (firm_fcst_nom / comp_fcst_nom) if comp_fcst_nom else None
            firm_yoy = rate
            # Premium = 0 by construction since both got same rate; but if rate were
            # sector-specific while firm followed its own trajectory, we'd compute it.
            # Here, keep premium as 0% for the forecast row since both use FMI rate.
            fcst_premium = 0.0

            rows.append(YearRow(
                year=fcst_year,
                rank=end_rank,  # assume rank holds steady
                firm_nom_m=firm_fcst_nom,
                firm_real_m=firm_fcst_real,
                comp_nom_m=comp_fcst_nom,
                comp_real_m=comp_fcst_real,
                share=fcst_share,
                yoy_premium=fcst_premium,
                is_forecast=True,
            ))

    return SectionFacts(
        firm_short=firm_short,
        sector_label=sector_label,
        sector_key=sector_key,
        base_year=base_year,
        start_year=int(start_row["data_year"]),
        end_year=int(end_row["data_year"]),
        start_nom_m=f_start_nom, end_nom_m=f_end_nom,
        start_real_m=f_start_real, end_real_m=f_end_real,
        nominal_growth_pct=nominal_growth_pct,
        nominal_cagr_pct=nominal_cagr_pct,
        real_cagr_pct=real_cagr_pct,
        comp_start_nom_m=c_start_nom, comp_end_nom_m=c_end_nom,
        comp_nominal_cagr_pct=comp_nominal_cagr_pct,
        comp_real_cagr_pct=comp_real_cagr_pct,
        cagr_premium_pct=cagr_premium_pct,
        start_share=start_share, end_share=end_share,
        share_change_bps=share_change_bps,
        best_share_year=best_share_year, best_share_value=best_share_value,
        worst_share_year=worst_share_year, worst_share_value=worst_share_value,
        start_pct_of_firm_total=start_pct_of_total,
        end_pct_of_firm_total=end_pct_of_total,
        largest_firm_yoy_real=largest_firm_yoy_real,
        largest_firm_premium_year=largest_firm_premium_year,
        most_negative_firm_yoy_real=most_negative_firm_yoy_real,
        firm_2009_real_yoy=firm_2009_real_yoy,
        comp_2009_real_yoy=comp_2009_real_yoy,
        firm_2009_premium=firm_2009_premium,
        start_rank=start_rank, end_rank=end_rank,
        best_rank=best_rank, best_rank_year=best_rank_year,
        rows=rows,
    )


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(HERE / "lib"))
    from ingest import build_panel, load_cci_annual
    from resolve import get_firm_panel, resolve as resolve_fn
    from charts import build_composite_by_year

    panel = build_panel(HERE / "data" / "enr")
    cci = load_cci_annual(HERE / "data" / "cci.xlsx", base_year=2025)
    cci_lookup = dict(zip(cci["year"], cci["deflator"]))
    composite_by_year = build_composite_by_year(panel)

    match = resolve_fn(panel, "HDR", interactive=False)
    firm_data = get_firm_panel(panel, match)

    facts = compute_section_facts(
        firm_data=firm_data, composite_by_year=composite_by_year,
        sector_key="power", sector_label="Power", firm_short="HDR",
        cci_lookup=cci_lookup,
    )
    d = facts.to_dict()
    # Drop the rows for printing (they're long)
    d_summary = {k: v for k, v in d.items() if k != "rows"}
    print(json.dumps(d_summary, indent=2, default=str))
    print(f"\n(Plus {len(d['rows'])} year rows in 'rows')")
