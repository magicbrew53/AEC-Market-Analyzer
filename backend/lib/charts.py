"""
Charts in the HDR report's visual style.

Five chart types:
  1. nominal_dual_axis      — firm $M (right) vs. composite $B (left), nominal
  2. real_dual_axis         — same but constant-base-year $
  3. yoy_paired_bars        — paired YoY % bars (firm vs. composite), nominal or real
  4. market_share_line      — firm's % share of composite, single line
  5. comparison_table_image — used elsewhere (docx_render handles tables natively)

Each sector has its own primary color for the firm; the composite is rendered
as the same hue but rendered DASHED on lines or in lighter shade on bars.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

# Suppress noisy matplotlib warnings about NaN values for sectors where the
# firm has zero/no participation (Manufacturing, Hazardous Waste, etc. for HDR).
warnings.filterwarnings("ignore", category=RuntimeWarning, module="matplotlib")

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd


# ---------- Style constants ----------

CHART_BG = "#F2F2F2"
GRID_COLOR = "#FFFFFF"
FORECAST_BG = "#FFF3DC"
FORECAST_BORDER = "#C9A96A"
FORECAST_LABEL_COLOR = "#A88846"

DEFAULT_FIG_SIZE = (8.5, 4.0)


@dataclass
class SectorPalette:
    primary: str
    composite: str
    fill: str


_PALETTES: dict[str, SectorPalette] = {
    "total":          SectorPalette(primary="#D62828", composite="#2C3E50", fill="#FBE4E4"),
    "intl":           SectorPalette(primary="#1F6FB4", composite="#2C3E50", fill="#DCE9F4"),
    "gen_bldg":       SectorPalette(primary="#5D6D7E", composite="#2C3E50", fill="#E1E5EA"),
    "manufacturing":  SectorPalette(primary="#7F4F8F", composite="#2C3E50", fill="#EBDFEF"),
    "power":          SectorPalette(primary="#F39C12", composite="#D87F00", fill="#FDEBD0"),
    "water_supply":   SectorPalette(primary="#2E86AB", composite="#1B4F66", fill="#D6E9F2"),
    "sewer_waste":    SectorPalette(primary="#117A65", composite="#0B5345", fill="#D1E8E2"),
    "ind_pet":        SectorPalette(primary="#7B5E3F", composite="#4E3B25", fill="#E8DDD0"),
    "transportation": SectorPalette(primary="#922B3E", composite="#5C1A26", fill="#F4DCE0"),
    "haz_waste":      SectorPalette(primary="#6E2C00", composite="#3B1700", fill="#F0DCC8"),
    "telecom":        SectorPalette(primary="#1B7A4A", composite="#0E4D2C", fill="#D4EBDB"),
    "other":          SectorPalette(primary="#5D5C61", composite="#2E2D30", fill="#E2E1E3"),
}


def palette_for(sector: str | None) -> SectorPalette:
    if not sector or sector == "total":
        return _PALETTES["total"]
    return _PALETTES.get(sector, SectorPalette(primary="#34495E", composite="#1B2631", fill="#D5DBDB"))


# ---------- Helpers ----------


def _apply_chart_style(ax: plt.Axes) -> None:
    ax.set_facecolor(CHART_BG)
    ax.grid(True, color=GRID_COLOR, linewidth=1.0, zorder=0)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("#666666")
        ax.spines[spine].set_linewidth(0.5)
    ax.tick_params(axis="both", which="both", length=0, labelsize=9)


def _draw_forecast_band(ax, last_actual_year, end_year, label=True):
    if end_year <= last_actual_year:
        return
    ax.axvspan(last_actual_year + 0.5, end_year + 0.5,
               facecolor=FORECAST_BG, edgecolor="none", zorder=0.5, alpha=0.7)
    ax.axvline(last_actual_year + 0.5,
               color=FORECAST_BORDER, linestyle=(0, (3, 3)), linewidth=0.8, zorder=0.6)
    if label:
        _, ymax = ax.get_ylim()
        ax.text(last_actual_year + 0.7,
                ymax * 0.95 if ymax > 0 else ymax,
                "forecast", fontsize=8, style="italic",
                color=FORECAST_LABEL_COLOR, ha="left", va="top")


def _format_year_axis(ax, year_min, year_max, every=2):
    start = year_min if year_min % every == 0 else year_min + (every - year_min % every)
    ticks = list(range(start, year_max + 1, every))
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(t) for t in ticks])
    ax.set_xlim(year_min - 0.5, year_max + 0.5)


def _money_b(x, pos):
    return f"${x/1000:.0f}B" if x >= 1000 else f"${x:.0f}M"


def _money_m(x, pos):
    return f"${x:,.0f}M"


def _pct_axis(x, pos):
    if abs(x) < 0.10:
        return f"{x*100:.1f}%"
    return f"{x*100:.0f}%"


def _firm_col_for_sector(sector_key: str) -> str:
    if sector_key == "total" or sector_key is None:
        return "total_rev_m"
    if sector_key == "intl":
        return "intl_rev_m"
    return f"{sector_key}_m"


# ---------- Charts 1 & 2: nominal / real revenue, dual-axis ----------


def _revenue_dual_axis(firm_data, composite_data, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, out_path,
                       title_kind, deflate, cci_lookup=None):
    palette = palette_for(sector_key)
    firm_col = _firm_col_for_sector(sector_key)

    merged = pd.merge(
        firm_data[["data_year", firm_col]].rename(columns={firm_col: "firm"}),
        composite_data[["data_year", "value"]].rename(columns={"value": "comp"}),
        on="data_year", how="outer",
    ).sort_values("data_year").reset_index(drop=True)

    if deflate and cci_lookup:
        merged["firm"] = [
            v * cci_lookup.get(int(y), 1.0) if pd.notna(v) else v
            for y, v in zip(merged["data_year"], merged["firm"])
        ]
        merged["comp"] = [
            v * cci_lookup.get(int(y), 1.0) if pd.notna(v) else v
            for y, v in zip(merged["data_year"], merged["comp"])
        ]

    year_min = int(merged["data_year"].min()) - 1
    year_max = int(merged["data_year"].max())
    if forecast_year and forecast_year > year_max:
        year_max = forecast_year

    full = pd.DataFrame({"data_year": list(range(year_min + 1, year_max + 1))})
    merged = full.merge(merged, on="data_year", how="left")

    fig, ax_left = plt.subplots(figsize=DEFAULT_FIG_SIZE, dpi=120)
    ax_right = ax_left.twinx()

    _apply_chart_style(ax_left)
    ax_right.grid(False)
    ax_right.set_facecolor("none")
    for spine in ["top", "left", "bottom"]:
        ax_right.spines[spine].set_visible(False)
    ax_right.spines["right"].set_visible(True)
    ax_right.spines["right"].set_color(palette.primary)
    ax_right.spines["right"].set_linewidth(0.8)
    ax_right.tick_params(axis="y", colors=palette.primary, labelsize=9, length=0)

    ax_left.plot(merged["data_year"], merged["comp"],
                 color=palette.composite, linewidth=1.8, linestyle=(0, (5, 3)),
                 marker="o", markersize=4,
                 markerfacecolor=palette.composite, markeredgecolor=palette.composite,
                 label="ENR Composite (left)", zorder=3)

    ax_right.plot(merged["data_year"], merged["firm"],
                  color=palette.primary, linewidth=2.0,
                  marker="o", markersize=4,
                  markerfacecolor=palette.primary, markeredgecolor=palette.primary,
                  label=f"{firm_short} (right)", zorder=4)
    ax_right.fill_between(merged["data_year"], 0, merged["firm"].fillna(0),
                          color=palette.fill, alpha=0.7, zorder=2)

    ax_left.yaxis.set_major_formatter(mticker.FuncFormatter(_money_b))
    ax_right.yaxis.set_major_formatter(mticker.FuncFormatter(_money_m))
    ax_left.set_ylabel("ENR Composite ($B)", fontsize=9, color="#333333")
    ax_right.set_ylabel(f"{firm_short} ($M)", fontsize=9, color=palette.primary,
                        rotation=270, labelpad=15)
    ax_left.set_ylim(bottom=0)
    ax_right.set_ylim(bottom=0)
    if merged["comp"].notna().any():
        ax_left.set_ylim(top=merged["comp"].max() * 1.10)
    if merged["firm"].notna().any():
        ax_right.set_ylim(top=merged["firm"].max() * 1.10)

    _format_year_axis(ax_left, year_min, year_max, every=2)

    last_actual_row = merged[merged["data_year"] == last_actual_year]
    if not last_actual_row.empty and pd.notna(last_actual_row["firm"].iloc[0]):
        last_val = float(last_actual_row["firm"].iloc[0])
        ax_right.annotate(
            f"${last_val:,.0f}M",
            xy=(last_actual_year, last_val),
            xytext=(8, 0), textcoords="offset points",
            fontsize=9, color=palette.primary, fontweight="bold", va="center")

    if forecast_year and forecast_year > last_actual_year:
        _draw_forecast_band(ax_left, last_actual_year, year_max, label=True)

    span_label = f"{year_min}–{year_max}"
    title = f"{sector_label} — {firm_short} vs. ENR Composite ({title_kind}), {span_label}"
    ax_left.set_title(title, fontsize=12, fontweight="bold", pad=10, color="#1A1A1A")

    h1, l1 = ax_left.get_legend_handles_labels()
    h2, l2 = ax_right.get_legend_handles_labels()
    ax_left.legend(h1 + h2, l1 + l2, loc="upper left",
                   fontsize=9, frameon=False, borderaxespad=0.5)

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white", dpi=120)
    plt.close(fig)


def chart_nominal_revenue(firm_data, composite_data, firm_short, sector_label, sector_key,
                          last_actual_year, forecast_year, out_path):
    _revenue_dual_axis(firm_data, composite_data, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, out_path,
                       title_kind="Nominal $", deflate=False)


def chart_real_revenue(firm_data, composite_data, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, out_path, cci_lookup, base_year=2025):
    _revenue_dual_axis(firm_data, composite_data, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, out_path,
                       title_kind=f"Constant {base_year}$", deflate=True,
                       cci_lookup=cci_lookup)


# ---------- Charts 3 & 4: YoY paired bars ----------


def _yoy_paired_bars(firm_data, composite_data, firm_short, sector_label, sector_key,
                     last_actual_year, forecast_year, out_path, deflate, cci_lookup=None):
    palette = palette_for(sector_key)
    firm_col = _firm_col_for_sector(sector_key)

    merged = pd.merge(
        firm_data[["data_year", firm_col]].rename(columns={firm_col: "firm"}),
        composite_data[["data_year", "value"]].rename(columns={"value": "comp"}),
        on="data_year", how="outer",
    ).sort_values("data_year").reset_index(drop=True)

    if deflate and cci_lookup:
        merged["firm"] = [
            v * cci_lookup.get(int(y), 1.0) if pd.notna(v) else v
            for y, v in zip(merged["data_year"], merged["firm"])
        ]
        merged["comp"] = [
            v * cci_lookup.get(int(y), 1.0) if pd.notna(v) else v
            for y, v in zip(merged["data_year"], merged["comp"])
        ]

    merged["firm_yoy"] = merged["firm"].pct_change()
    merged["comp_yoy"] = merged["comp"].pct_change()
    merged = merged.dropna(subset=["firm_yoy", "comp_yoy"], how="all").reset_index(drop=True)

    year_min = int(merged["data_year"].min()) - 1
    year_max = int(merged["data_year"].max())
    if forecast_year and forecast_year > year_max:
        year_max = forecast_year

    fig, ax = plt.subplots(figsize=DEFAULT_FIG_SIZE, dpi=120)
    _apply_chart_style(ax)

    bar_w = 0.4
    x = merged["data_year"].astype(int)
    ax.bar(x - bar_w / 2, merged["comp_yoy"], width=bar_w,
           color=palette.primary, alpha=0.55, edgecolor="none",
           label="ENR Composite YoY", zorder=3)
    ax.bar(x + bar_w / 2, merged["firm_yoy"], width=bar_w,
           color=palette.primary, alpha=1.0, edgecolor="none",
           label=f"{firm_short} YoY", zorder=4)

    ax.axhline(0, color="#333333", linewidth=0.7, zorder=2)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct_axis))
    _format_year_axis(ax, year_min, year_max, every=2)

    title_kind = "Real 2025$" if deflate else "Nominal"
    title = f"{sector_label} — YoY Growth ({title_kind}), {firm_short} vs. ENR Composite"
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10, color="#1A1A1A")
    ax.legend(loc="upper left", fontsize=9, frameon=False, borderaxespad=0.5)

    if forecast_year and forecast_year > last_actual_year:
        _draw_forecast_band(ax, last_actual_year, year_max, label=True)

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white", dpi=120)
    plt.close(fig)


def chart_yoy_nominal(firm_data, composite_data, firm_short, sector_label, sector_key,
                     last_actual_year, forecast_year, out_path):
    _yoy_paired_bars(firm_data, composite_data, firm_short, sector_label, sector_key,
                    last_actual_year, forecast_year, out_path, deflate=False)


def chart_yoy_real(firm_data, composite_data, firm_short, sector_label, sector_key,
                  last_actual_year, forecast_year, out_path, cci_lookup):
    _yoy_paired_bars(firm_data, composite_data, firm_short, sector_label, sector_key,
                    last_actual_year, forecast_year, out_path, deflate=True,
                    cci_lookup=cci_lookup)


# ---------- Chart 5: Market share line ----------


def chart_market_share(firm_data, composite_data, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, out_path):
    palette = palette_for(sector_key)
    firm_col = _firm_col_for_sector(sector_key)

    merged = pd.merge(
        firm_data[["data_year", firm_col]].rename(columns={firm_col: "firm"}),
        composite_data[["data_year", "value"]].rename(columns={"value": "comp"}),
        on="data_year", how="outer",
    ).sort_values("data_year").reset_index(drop=True)

    merged["share"] = merged["firm"] / merged["comp"]

    year_min = int(merged["data_year"].min()) - 1
    year_max = int(merged["data_year"].max())
    if forecast_year and forecast_year > year_max:
        year_max = forecast_year

    full = pd.DataFrame({"data_year": list(range(year_min + 1, year_max + 1))})
    merged = full.merge(merged, on="data_year", how="left")

    fig, ax = plt.subplots(figsize=DEFAULT_FIG_SIZE, dpi=120)
    _apply_chart_style(ax)

    ax.plot(merged["data_year"], merged["share"],
            color=palette.primary, linewidth=2.0,
            marker="o", markersize=4,
            markerfacecolor=palette.primary, markeredgecolor=palette.primary,
            zorder=4)
    ax.fill_between(merged["data_year"], 0, merged["share"].fillna(0),
                    color=palette.fill, alpha=0.7, zorder=2)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct_axis))
    ax.set_ylim(bottom=0)
    if merged["share"].notna().any():
        ax.set_ylim(top=max(merged["share"].max() * 1.15, 0.005))

    _format_year_axis(ax, year_min, year_max, every=2)

    valid = merged.dropna(subset=["share"])
    if not valid.empty:
        first = valid.iloc[0]
        ax.annotate(
            f"{first['share']*100:.2f}%",
            xy=(first["data_year"], first["share"]),
            xytext=(0, 8), textcoords="offset points",
            fontsize=9, color=palette.primary, fontweight="bold", ha="center",
        )
        last_actual_row = valid[valid["data_year"] == last_actual_year]
        if not last_actual_row.empty:
            last = last_actual_row.iloc[0]
            ax.annotate(
                f"{last['share']*100:.2f}%",
                xy=(last["data_year"], last["share"]),
                xytext=(0, 8), textcoords="offset points",
                fontsize=9, color=palette.primary, fontweight="bold", ha="center",
            )

    title = f"{firm_short} Market Share — {sector_label}, {year_min}–{year_max}"
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10, color="#1A1A1A")

    if forecast_year and forecast_year > last_actual_year:
        _draw_forecast_band(ax, last_actual_year, year_max, label=False)

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white", dpi=120)
    plt.close(fig)


# ---------- Render all 5 charts for a sector ----------


def render_sector_charts(firm_data, composite_by_year, firm_short, sector_label,
                         sector_key, last_actual_year, forecast_year, output_dir, cci_lookup,
                         fmi_forecast=None):
    """
    Render all 5 charts for a sector. If fmi_forecast is provided AND has a rate
    for this sector, append a forecast point at (last_actual_year + 1) to both
    the firm and composite series before plotting. The forecast point is
    visually distinct via the existing forecast band.
    """
    from pathlib import Path
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if sector_key == "total":
        comp_col = "total"
    elif sector_key == "intl":
        comp_col = "intl"
    else:
        comp_col = sector_key
    composite_series = composite_by_year[["data_year", comp_col]].rename(columns={comp_col: "value"})

    # Inject forecast point if available
    if fmi_forecast and fmi_forecast.rates and forecast_year:
        rate = fmi_forecast.rates.get(sector_key)
        if rate is not None:
            firm_col = _firm_col_for_sector(sector_key)
            last_firm_row = firm_data[firm_data["data_year"] == last_actual_year]
            last_comp_row = composite_series[composite_series["data_year"] == last_actual_year]
            if not last_firm_row.empty and pd.notna(last_firm_row[firm_col].iloc[0]):
                last_firm_val = float(last_firm_row[firm_col].iloc[0])
                fcst_firm_val = last_firm_val * (1 + rate)
                # Append a row to firm_data with the forecast value
                fcst_row = {col: None for col in firm_data.columns}
                fcst_row["data_year"] = forecast_year
                fcst_row[firm_col] = fcst_firm_val
                firm_data = pd.concat([firm_data, pd.DataFrame([fcst_row])], ignore_index=True)
            if not last_comp_row.empty and pd.notna(last_comp_row["value"].iloc[0]):
                last_comp_val = float(last_comp_row["value"].iloc[0])
                fcst_comp_val = last_comp_val * (1 + rate)
                composite_series = pd.concat(
                    [composite_series, pd.DataFrame([{"data_year": forecast_year, "value": fcst_comp_val}])],
                    ignore_index=True,
                )

    paths = {}
    safe = sector_key.replace("/", "_")

    p = output_dir / f"{firm_short}_{safe}_01_nominal.png"
    chart_nominal_revenue(firm_data, composite_series, firm_short, sector_label, sector_key,
                          last_actual_year, forecast_year, str(p))
    paths["nominal_revenue"] = str(p)

    p = output_dir / f"{firm_short}_{safe}_02_real.png"
    chart_real_revenue(firm_data, composite_series, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, str(p), cci_lookup)
    paths["real_revenue"] = str(p)

    p = output_dir / f"{firm_short}_{safe}_03_yoy_nominal.png"
    chart_yoy_nominal(firm_data, composite_series, firm_short, sector_label, sector_key,
                     last_actual_year, forecast_year, str(p))
    paths["yoy_nominal"] = str(p)

    p = output_dir / f"{firm_short}_{safe}_04_yoy_real.png"
    chart_yoy_real(firm_data, composite_series, firm_short, sector_label, sector_key,
                  last_actual_year, forecast_year, str(p), cci_lookup)
    paths["yoy_real"] = str(p)

    p = output_dir / f"{firm_short}_{safe}_05_market_share.png"
    chart_market_share(firm_data, composite_series, firm_short, sector_label, sector_key,
                       last_actual_year, forecast_year, str(p))
    paths["market_share"] = str(p)

    return paths


def build_composite_by_year(panel: pd.DataFrame) -> pd.DataFrame:
    sector_cols = [
        "gen_bldg_m", "manufacturing_m", "power_m", "water_supply_m",
        "sewer_waste_m", "ind_pet_m", "transportation_m", "haz_waste_m",
        "telecom_m", "other_m",
    ]
    agg = {"total_rev_m": "sum", "intl_rev_m": "sum"}
    for c in sector_cols:
        agg[c] = "sum"

    g = panel.groupby("data_year").agg(agg).reset_index()
    g = g.rename(columns={
        "total_rev_m": "total",
        "intl_rev_m": "intl",
        "gen_bldg_m": "gen_bldg",
        "manufacturing_m": "manufacturing",
        "power_m": "power",
        "water_supply_m": "water_supply",
        "sewer_waste_m": "sewer_waste",
        "ind_pet_m": "ind_pet",
        "transportation_m": "transportation",
        "haz_waste_m": "haz_waste",
        "telecom_m": "telecom",
        "other_m": "other",
    })
    return g


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(HERE / "lib"))
    from ingest import build_panel, load_cci_annual
    from resolve import get_firm_panel, resolve as resolve_fn

    panel = build_panel(HERE / "data" / "enr")
    cci = load_cci_annual(HERE / "data" / "cci.xlsx", base_year=2025)
    cci_lookup = dict(zip(cci["year"], cci["deflator"]))

    composite_by_year = build_composite_by_year(panel)

    match = resolve_fn(panel, "HDR", interactive=False)
    firm_data = get_firm_panel(panel, match)

    paths = render_sector_charts(
        firm_data=firm_data,
        composite_by_year=composite_by_year,
        firm_short="HDR",
        sector_label="Power",
        sector_key="power",
        last_actual_year=2025,
        forecast_year=2026,
        output_dir=HERE / "output" / "power_section",
        cci_lookup=cci_lookup,
    )
    for label, path in paths.items():
        print(f"  {label:<20} → {path}")
