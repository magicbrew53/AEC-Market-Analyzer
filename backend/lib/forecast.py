"""
forecast.py — generate the next-year forecast row for each section.

Don's HDR report includes a "2026F" row in every comparison table and a forecast
band on every chart. The forecast applies FMI Q1 quarterly growth rates per
sector to project nominal revenue one year forward.

This module reads forecast rates from data/fmi_forecast.json. Don edits this
file when FMI publishes a new quarterly report (typically once per quarter).

Schema of fmi_forecast.json:

{
  "publishedDate": "2026-04-15",
  "source": "FMI Q1 2026 North American E&C Industry Forecast",
  "forecastYear": 2026,
  "rates": {
    "total":          0.046,
    "intl":           0.030,
    "gen_bldg":       0.040,
    "manufacturing":  0.025,
    "power":          0.050,
    "water_supply":   0.060,
    "sewer_waste":    0.055,
    "ind_pet":        0.020,
    "transportation": 0.040,
    "haz_waste":      0.015,
    "telecom":        0.250,
    "other":          0.030
  }
}

If a sector's rate is missing, the forecast falls back to a 3-year CAGR
continuation from the firm's own data (per the methodology in the HDR report).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class FmiForecast:
    publishedDate: str
    source: str
    forecastYear: int
    rates: dict[str, float]


def load_fmi_forecast(path: Path | None) -> FmiForecast | None:
    if path is None or not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FmiForecast(
        publishedDate=raw.get("publishedDate", ""),
        source=raw.get("source", ""),
        forecastYear=int(raw.get("forecastYear")),
        rates=raw.get("rates", {}),
    )


def project_next_year_value(
    historical_series: pd.Series,
    last_year: int,
    sector_key: str,
    fmi: FmiForecast | None,
    fallback_cagr_years: int = 3,
) -> float | None:
    """
    Project the next year's nominal value for one series.

    historical_series should be a Series indexed by year with float values.
    Returns the projected value for last_year + 1.

    If FMI rate is available for the sector, applies it. Otherwise, computes
    the trailing N-year CAGR and applies that.
    """
    if last_year not in historical_series.index:
        return None
    last_val = historical_series.loc[last_year]
    if pd.isna(last_val) or last_val <= 0:
        return None

    rate = None
    if fmi and fmi.rates and sector_key in fmi.rates:
        rate = fmi.rates[sector_key]

    if rate is None:
        # Fallback: trailing CAGR
        start_year = last_year - fallback_cagr_years
        if start_year not in historical_series.index:
            return None
        start_val = historical_series.loc[start_year]
        if pd.isna(start_val) or start_val <= 0:
            return None
        rate = (last_val / start_val) ** (1 / fallback_cagr_years) - 1

    return float(last_val) * (1 + rate)


def write_default_fmi_file(out_path: Path) -> None:
    """Write a default FMI forecast file. Don edits these rates each quarter."""
    template = {
        "publishedDate": "<date FMI report was published>",
        "source": "<e.g. FMI Q1 2026 North American E&C Industry Forecast>",
        "forecastYear": 2026,
        "rates": {
            "_comment": "Annual nominal-revenue growth rate for each sector. Edit per FMI quarterly report. Set to null to use trailing-CAGR fallback.",
            "total":          0.046,
            "intl":           0.030,
            "gen_bldg":       0.040,
            "manufacturing":  0.025,
            "power":          0.050,
            "water_supply":   0.060,
            "sewer_waste":    0.055,
            "ind_pet":        0.020,
            "transportation": 0.040,
            "haz_waste":      0.015,
            "telecom":        0.250,
            "other":          0.030
        }
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fmi.json"
        write_default_fmi_file(p)
        f = load_fmi_forecast(p)
        print(f"Loaded forecast: {f.forecastYear}, {len(f.rates)} sector rates")

        # Smoke test projection
        s = pd.Series({2022: 100.0, 2023: 110.0, 2024: 120.0, 2025: 130.0})
        v = project_next_year_value(s, 2025, "power", f)
        print(f"Power 2026 projection (FMI rate 5%): {v:.2f}  (expected: 130 * 1.05 = 136.50)")

        v = project_next_year_value(s, 2025, "unknown_sector", f)
        print(f"Unknown sector projection (3yr CAGR fallback from 100→130): {v:.2f}")
