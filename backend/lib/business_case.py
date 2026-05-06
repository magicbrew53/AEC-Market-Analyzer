"""
business_case.py — picker, ROI math, market projection, and orchestrator
for the RevWin Business Case report.

The Business Case is a 4-6 page sales document that is produced AFTER the
50-page Market Analysis identifies a firm's sector trajectory. It picks ONE
sector (or a bundled pair) where capture-planning lift is most credible,
projects the addressable market to a target year, builds a 3-scenario ROI
table, and frames the Ask.

Public surface:
  - load_pilot_assumptions(path) -> PilotAssumptions
  - rank_sectors(...) -> list[SectorCandidate]
  - pick_sector(...) -> SectorPick
  - project_target_market_size(...) -> MarketProjection
  - build_roi_table(...) -> RoiTable
  - compute_active_growth_target(...) -> ActiveGrowthTarget | None
  - assemble_business_case(...) -> BusinessCaseInputs   ← top-level orchestrator
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from compute import SectionFacts, compute_section_facts


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------


@dataclass
class PilotAssumptions:
    default_pilot_duration_quarters: int
    default_target_year: int
    filter_thresholds: dict           # {min_end_revenue_m, min_end_share}
    sectors: dict                     # sector_key -> {avg_pursuit_fee_m, win_rate_uplift_pp, pilot_volume, pilot_cost_m}
    bundling_rules: dict              # sector_key -> {bundle_with: [keys], label: str}


@dataclass
class SectorCandidate:
    sector_key: str
    sector_label: str
    end_revenue_m: float
    end_share: float
    firm_real_cagr_pct: float
    composite_real_cagr_pct: float
    delta_pp: float                   # firm - composite (smaller = flatter)
    facts: SectionFacts


@dataclass
class SectorPick:
    sector_keys: list[str]            # 1 or 2 keys; element 0 is the primary
    sector_labels: list[str]
    display_label: str                # e.g. "Water (combined)" or "Power"
    primary: SectorCandidate
    secondary: Optional[SectorCandidate]
    rationale: str
    forced: bool = False


@dataclass
class MarketProjection:
    sector_keys: list[str]
    end_year: int
    target_year: int
    end_year_m: float
    target_year_m: float
    growth_m: float
    rate_per_year: float              # blended rate used


@dataclass
class RoiScenario:
    label: str                        # "Conservative" / "Mid" / "Aggressive"
    pilot_volume: int
    win_rate_uplift_pp: float
    avg_pursuit_fee_m: float
    pilot_cost_m: float
    incremental_wins: float
    incremental_fee_revenue_m: float
    first_cycle_roi: float            # revenue / pilot_cost


@dataclass
class RoiTable:
    sector_key_used: str              # primary sector used for economics
    pilot_duration_quarters: int
    scenarios: list[RoiScenario]      # always 3: Conservative / Mid / Aggressive


@dataclass
class ActiveGrowthTarget:
    has_explicit_target: bool
    sector_key_matched: Optional[str]
    target_share: Optional[float]
    target_revenue_m: Optional[float]
    current_revenue_m: Optional[float]
    net_new_required_m: Optional[float]
    rationale: Optional[str]


@dataclass
class BusinessCaseInputs:
    firm_short: str
    firm_legal_name: Optional[str]
    end_year: int                     # last actual data year (e.g. 2025)
    target_year: int                  # e.g. 2029
    primary_color_hex: str

    sector_pick: SectorPick
    market_projection: MarketProjection
    roi_table: RoiTable
    active_growth: ActiveGrowthTarget

    pilot_duration_quarters: int

    # Optional research tie-ins for narratives / leadership
    research: object = None           # FirmResearch | None

    def to_facts_dict(self) -> dict:
        """Strict facts JSON for the LLM. No prose, no narrative — numbers only."""
        sp = self.sector_pick
        mp = self.market_projection
        ag = self.active_growth
        rt = self.roi_table
        primary = sp.primary

        leaders = []
        if self.research and getattr(self.research, "strategicInitiative", None):
            leaders = list(self.research.strategicInitiative.growthOrgLeaders or [])

        return {
            "firm_short": self.firm_short,
            "firm_legal_name": self.firm_legal_name,
            "end_year": self.end_year,
            "target_year": self.target_year,
            "sector_keys": sp.sector_keys,
            "sector_labels": sp.sector_labels,
            "display_label": sp.display_label,
            "rationale": sp.rationale,
            "forced": sp.forced,
            "pilot_duration_quarters": self.pilot_duration_quarters,
            "primary_sector": {
                "sector_key": primary.sector_key,
                "sector_label": primary.sector_label,
                "end_revenue_m": primary.end_revenue_m,
                "end_share_pct": (primary.end_share or 0) * 100,
                "firm_real_cagr_pct": primary.firm_real_cagr_pct,
                "composite_real_cagr_pct": primary.composite_real_cagr_pct,
                "delta_pp": primary.delta_pp,
            },
            "secondary_sector": (
                {
                    "sector_key": sp.secondary.sector_key,
                    "sector_label": sp.secondary.sector_label,
                    "end_revenue_m": sp.secondary.end_revenue_m,
                    "end_share_pct": (sp.secondary.end_share or 0) * 100,
                    "firm_real_cagr_pct": sp.secondary.firm_real_cagr_pct,
                    "composite_real_cagr_pct": sp.secondary.composite_real_cagr_pct,
                    "delta_pp": sp.secondary.delta_pp,
                }
                if sp.secondary else None
            ),
            "current_combined_revenue_m": _combined_revenue_m(sp),
            "market_projection": {
                "end_year_m": mp.end_year_m,
                "target_year_m": mp.target_year_m,
                "growth_m": mp.growth_m,
                "rate_per_year_pct": mp.rate_per_year * 100,
            },
            "active_growth": {
                "has_explicit_target": ag.has_explicit_target,
                "target_share_pct": (ag.target_share * 100) if ag.target_share else None,
                "target_revenue_m": ag.target_revenue_m,
                "net_new_required_m": ag.net_new_required_m,
                "rationale": ag.rationale,
            },
            "roi_scenarios": [
                {
                    "label": s.label,
                    "pilot_volume": s.pilot_volume,
                    "win_rate_uplift_pp": s.win_rate_uplift_pp,
                    "avg_pursuit_fee_m": s.avg_pursuit_fee_m,
                    "pilot_cost_m": s.pilot_cost_m,
                    "incremental_wins": s.incremental_wins,
                    "incremental_fee_revenue_m": s.incremental_fee_revenue_m,
                    "first_cycle_roi_x": s.first_cycle_roi,
                }
                for s in rt.scenarios
            ],
            "growth_org_leaders": leaders,
        }


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _combined_revenue_m(pick: SectorPick) -> float:
    total = pick.primary.end_revenue_m or 0.0
    if pick.secondary:
        total += pick.secondary.end_revenue_m or 0.0
    return total


def _trailing_cagr(series: pd.Series, last_year: int, n_years: int = 3) -> Optional[float]:
    if last_year not in series.index:
        return None
    end_val = series.loc[last_year]
    start_year = last_year - n_years
    if start_year not in series.index:
        return None
    start_val = series.loc[start_year]
    if pd.isna(start_val) or pd.isna(end_val) or start_val <= 0 or end_val <= 0:
        return None
    return float((end_val / start_val) ** (1 / n_years) - 1)


# ---------------------------------------------------------------------------
#  Loaders
# ---------------------------------------------------------------------------


def load_pilot_assumptions(path: Path) -> PilotAssumptions:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return PilotAssumptions(
        default_pilot_duration_quarters=int(raw.get("default_pilot_duration_quarters", 6)),
        default_target_year=int(raw.get("default_target_year", 2029)),
        filter_thresholds={
            "min_end_revenue_m": float(raw.get("filter_thresholds", {}).get("min_end_revenue_m", 50)),
            "min_end_share":     float(raw.get("filter_thresholds", {}).get("min_end_share", 0.01)),
        },
        sectors=raw.get("sectors", {}),
        bundling_rules=raw.get("bundling_rules", {}),
    )


# ---------------------------------------------------------------------------
#  Picker
# ---------------------------------------------------------------------------


def rank_sectors(
    firm_data: pd.DataFrame,
    composite_by_year: pd.DataFrame,
    section_order: list[tuple[str, str]],
    firm_short: str,
    cci_lookup: dict,
    base_year: int,
    start_year: int,
    end_year: int,
    thresholds: dict,
    always_include: Optional[list[str]] = None,
) -> list[SectorCandidate]:
    """
    Compute facts for every named sector, filter to those passing the
    size/share thresholds, sort ASC by (firm_real_cagr - comp_real_cagr).

    Skips 'total' and 'intl' (those are not pilotable sectors).

    `always_include` keys (e.g. user-forced sectors) bypass the size/share
    filter so a deliberate override still works on small firms.
    """
    min_rev = thresholds["min_end_revenue_m"]
    min_share = thresholds["min_end_share"]
    always_include_set = set(always_include or [])

    candidates: list[SectorCandidate] = []
    for sector_key, sector_label in section_order:
        if sector_key in ("total", "intl"):
            continue
        try:
            facts = compute_section_facts(
                firm_data=firm_data,
                composite_by_year=composite_by_year,
                sector_key=sector_key,
                sector_label=sector_label,
                firm_short=firm_short,
                cci_lookup=cci_lookup,
                base_year=base_year,
                start_year=start_year,
                end_year=end_year,
                fmi_forecast=None,
            )
        except Exception:
            continue

        if facts.end_nom_m is None or facts.end_share is None:
            continue
        if facts.real_cagr_pct is None or facts.comp_real_cagr_pct is None:
            continue

        passes_filter = (facts.end_nom_m >= min_rev and facts.end_share >= min_share)
        if not passes_filter and sector_key not in always_include_set:
            continue

        delta_pp = facts.real_cagr_pct - facts.comp_real_cagr_pct
        candidates.append(SectorCandidate(
            sector_key=sector_key,
            sector_label=sector_label,
            end_revenue_m=float(facts.end_nom_m),
            end_share=float(facts.end_share),
            firm_real_cagr_pct=float(facts.real_cagr_pct),
            composite_real_cagr_pct=float(facts.comp_real_cagr_pct),
            delta_pp=float(delta_pp),
            facts=facts,
        ))

    candidates.sort(key=lambda c: c.delta_pp)
    return candidates


def pick_sector(
    candidates: list[SectorCandidate],
    bundling_rules: dict,
    forced_keys: Optional[list[str]] = None,
    firm_short: str = "the firm",
) -> SectorPick:
    """
    Choose the pilot sector. If forced_keys is provided (1 or 2 keys), use those
    directly (looking them up in candidates if available; otherwise re-resolving
    via the supplied list and tolerating one missing). Otherwise auto-pick the
    flattest passing sector and try to bundle.
    """
    if not candidates and not forced_keys:
        raise ValueError(
            "No sectors passed the size/share filter (>= $50M revenue, >= 1% share). "
            "This usually means the firm is too small for the auto-picker. "
            "Pick a specific sector via the Sector override dropdown — forced "
            "sectors bypass the filter."
        )

    by_key = {c.sector_key: c for c in candidates}

    if forced_keys:
        forced_keys = [k for k in forced_keys if k]
        if not forced_keys:
            raise ValueError("Sector override specified but contained no valid keys.")

        primary_key = forced_keys[0]
        if primary_key not in by_key:
            raise ValueError(
                f"Sector '{primary_key}' has no ENR data for this firm — "
                f"the sector exists in the schema but the firm reported zero "
                f"or no rows for it. Pick a different sector."
            )
        primary = by_key[primary_key]

        secondary = None
        if len(forced_keys) >= 2:
            secondary_key = forced_keys[1]
            if secondary_key in by_key:
                secondary = by_key[secondary_key]

        sector_keys = [primary.sector_key]
        sector_labels = [primary.sector_label]
        if secondary:
            sector_keys.append(secondary.sector_key)
            sector_labels.append(secondary.sector_label)

        display_label = _resolve_display_label(sector_keys, bundling_rules, sector_labels)
        rationale = _build_rationale(primary, secondary, firm_short, forced=True)

        return SectorPick(
            sector_keys=sector_keys,
            sector_labels=sector_labels,
            display_label=display_label,
            primary=primary,
            secondary=secondary,
            rationale=rationale,
            forced=True,
        )

    # Auto-pick: flattest first
    primary = candidates[0]
    secondary: Optional[SectorCandidate] = None
    rule = bundling_rules.get(primary.sector_key, {})
    bundle_partners = rule.get("bundle_with", []) or []
    for partner_key in bundle_partners:
        if partner_key in by_key and partner_key != primary.sector_key:
            secondary = by_key[partner_key]
            break

    sector_keys = [primary.sector_key]
    sector_labels = [primary.sector_label]
    if secondary:
        sector_keys.append(secondary.sector_key)
        sector_labels.append(secondary.sector_label)

    display_label = _resolve_display_label(sector_keys, bundling_rules, sector_labels)
    rationale = _build_rationale(primary, secondary, firm_short, forced=False)

    return SectorPick(
        sector_keys=sector_keys,
        sector_labels=sector_labels,
        display_label=display_label,
        primary=primary,
        secondary=secondary,
        rationale=rationale,
        forced=False,
    )


def _resolve_display_label(sector_keys: list[str], bundling_rules: dict, sector_labels: list[str]) -> str:
    """Use bundling_rules label when bundled; else the single sector_label."""
    if len(sector_keys) >= 2:
        rule = bundling_rules.get(sector_keys[0], {})
        return rule.get("label") or " + ".join(sector_labels)
    rule = bundling_rules.get(sector_keys[0], {})
    return rule.get("label") or sector_labels[0]


def _build_rationale(
    primary: SectorCandidate,
    secondary: Optional[SectorCandidate],
    firm_short: str,
    forced: bool,
) -> str:
    delta = primary.delta_pp
    label = primary.sector_label
    if delta < 0:
        body = (
            f"{label} has the largest real-CAGR shortfall vs. the ENR Composite "
            f"({delta:+.1f}pp) — {firm_short} is genuinely losing ground in real "
            f"terms in this sector despite participating in a funded market."
        )
    else:
        body = (
            f"{label} has the smallest real-CAGR margin over the ENR Composite "
            f"({delta:+.1f}pp) — meaning it is {firm_short}'s flattest sector "
            f"relative to peers. The structural opportunity for capture-planning "
            f"lift is largest where the firm is least already gaining ground."
        )
    if secondary is not None:
        body += (
            f" {secondary.sector_label} shares the same buyer architecture and "
            f"also passes the size/share filter, so the two are bundled into a "
            f"single pilot."
        )
    if forced:
        body = "Sector forced via --sector. " + body
    return body


# ---------------------------------------------------------------------------
#  Market projection
# ---------------------------------------------------------------------------


def project_target_market_size(
    composite_by_year: pd.DataFrame,
    sector_keys: list[str],
    end_year: int,
    target_year: int,
    fmi_forecast,
) -> MarketProjection:
    """
    Project the composite (addressable market) for the given sector_keys
    from end_year to target_year. Sums across keys for bundles.
    """
    if target_year <= end_year:
        raise ValueError(f"target_year ({target_year}) must be > end_year ({end_year})")
    n_compounding = target_year - end_year

    end_total = 0.0
    target_total = 0.0
    rates: list[float] = []

    for key in sector_keys:
        if key not in composite_by_year.columns:
            continue
        series = pd.Series(composite_by_year[key].values, index=composite_by_year["data_year"].values)
        if end_year not in series.index:
            continue
        end_val = float(series.loc[end_year])
        if pd.isna(end_val) or end_val <= 0:
            continue

        rate = None
        if fmi_forecast and fmi_forecast.rates and key in fmi_forecast.rates:
            rate = fmi_forecast.rates.get(key)
        if rate is None:
            rate = _trailing_cagr(series.dropna(), end_year, n_years=3)
        if rate is None:
            rate = 0.0

        target_val = end_val * ((1 + rate) ** n_compounding)
        end_total += end_val
        target_total += target_val
        rates.append(float(rate))

    blended_rate = sum(rates) / len(rates) if rates else 0.0
    return MarketProjection(
        sector_keys=list(sector_keys),
        end_year=end_year,
        target_year=target_year,
        end_year_m=end_total,
        target_year_m=target_total,
        growth_m=target_total - end_total,
        rate_per_year=blended_rate,
    )


# ---------------------------------------------------------------------------
#  ROI table
# ---------------------------------------------------------------------------


_SCENARIO_LABELS = ("Conservative", "Mid", "Aggressive")


def build_roi_table(
    sector_keys: list[str],
    assumptions: PilotAssumptions,
    per_firm_override: Optional[dict] = None,
    pilot_volume_mid_override: Optional[int] = None,
    win_rate_uplift_mid_pp_override: Optional[float] = None,
    pilot_duration_quarters_override: Optional[int] = None,
) -> RoiTable:
    """Build the 3-scenario ROI table using the primary sector's economics.

    The Mid-scenario overrides scale the Conservative and Aggressive scenarios
    proportionally, preserving the relative spread baked into the assumption
    arrays. (e.g., if the original mid uplift is 12pp and the user overrides to
    18pp, the conservative and aggressive scale by 1.5×.)
    """
    primary_key = sector_keys[0]

    sector_econ = dict(assumptions.sectors.get(primary_key, {}))
    if not sector_econ:
        raise ValueError(
            f"No pilot economics defined for sector '{primary_key}' in "
            f"revwin_pilot_assumptions.json. Add the four arrays to .sectors."
        )

    if per_firm_override and primary_key in per_firm_override:
        for arr_name in ("avg_pursuit_fee_m", "win_rate_uplift_pp", "pilot_volume", "pilot_cost_m"):
            override_val = per_firm_override[primary_key].get(arr_name)
            if override_val is not None:
                sector_econ[arr_name] = override_val

    fees = list(sector_econ["avg_pursuit_fee_m"])
    uplifts = list(sector_econ["win_rate_uplift_pp"])
    volumes = list(sector_econ["pilot_volume"])
    costs = list(sector_econ["pilot_cost_m"])

    # Apply Mid-scenario overrides, scaling Cons / Agg proportionally
    if pilot_volume_mid_override is not None and volumes[1] > 0:
        scale = pilot_volume_mid_override / volumes[1]
        volumes = [int(round(v * scale)) for v in volumes]
        volumes[1] = int(pilot_volume_mid_override)
    if win_rate_uplift_mid_pp_override is not None and uplifts[1] > 0:
        scale = win_rate_uplift_mid_pp_override / uplifts[1]
        uplifts = [float(u * scale) for u in uplifts]
        uplifts[1] = float(win_rate_uplift_mid_pp_override)

    if not (len(fees) == len(uplifts) == len(volumes) == len(costs) == 3):
        raise ValueError(
            f"Sector '{primary_key}' pilot economics must each be 3-element arrays "
            f"(Conservative, Mid, Aggressive). Got: fees={len(fees)}, "
            f"uplifts={len(uplifts)}, volumes={len(volumes)}, costs={len(costs)}"
        )

    scenarios: list[RoiScenario] = []
    for i, label in enumerate(_SCENARIO_LABELS):
        volume = volumes[i]
        uplift_pp = uplifts[i]
        fee_m = fees[i]
        cost_m = costs[i]

        incremental_wins = volume * (uplift_pp / 100.0)
        incremental_revenue = incremental_wins * fee_m
        roi = (incremental_revenue / cost_m) if cost_m else 0.0

        scenarios.append(RoiScenario(
            label=label,
            pilot_volume=int(volume),
            win_rate_uplift_pp=float(uplift_pp),
            avg_pursuit_fee_m=float(fee_m),
            pilot_cost_m=float(cost_m),
            incremental_wins=float(incremental_wins),
            incremental_fee_revenue_m=float(incremental_revenue),
            first_cycle_roi=float(roi),
        ))

    duration_q = (
        int(pilot_duration_quarters_override)
        if pilot_duration_quarters_override is not None
        else int(assumptions.default_pilot_duration_quarters)
    )
    return RoiTable(
        sector_key_used=primary_key,
        pilot_duration_quarters=duration_q,
        scenarios=scenarios,
    )


# ---------------------------------------------------------------------------
#  Active growth target
# ---------------------------------------------------------------------------


def compute_active_growth_target(
    sector_pick: SectorPick,
    market_projection: MarketProjection,
    research,
    target_year: int,
) -> ActiveGrowthTarget:
    """
    Look up an explicit target share in research.fmiTargets[key].share<targetYear>.
    Returns a stub (has_explicit_target=False) if no match — the renderer falls
    back to "Baseline Maintain-Share" framing.
    """
    current_revenue_m = (sector_pick.primary.end_revenue_m or 0.0)
    if sector_pick.secondary:
        current_revenue_m += sector_pick.secondary.end_revenue_m or 0.0

    if research is None or not getattr(research, "fmiTargets", None):
        return ActiveGrowthTarget(
            has_explicit_target=False,
            sector_key_matched=None, target_share=None,
            target_revenue_m=None, current_revenue_m=current_revenue_m,
            net_new_required_m=None, rationale=None,
        )

    share_field = f"share{target_year}"
    matched_key: Optional[str] = None
    matched_share: Optional[float] = None
    matched_rationale: Optional[str] = None

    for key in sector_pick.sector_keys:
        block = research.fmiTargets.get(key)
        if not block:
            continue
        share = block.get(share_field)
        if share is None:
            continue
        try:
            matched_share = float(share)
        except (TypeError, ValueError):
            continue
        matched_key = key
        matched_rationale = block.get("rationale")
        break

    if matched_share is None:
        return ActiveGrowthTarget(
            has_explicit_target=False,
            sector_key_matched=None, target_share=None,
            target_revenue_m=None, current_revenue_m=current_revenue_m,
            net_new_required_m=None, rationale=None,
        )

    target_revenue_m = matched_share * market_projection.target_year_m
    net_new = target_revenue_m - current_revenue_m

    return ActiveGrowthTarget(
        has_explicit_target=True,
        sector_key_matched=matched_key,
        target_share=matched_share,
        target_revenue_m=target_revenue_m,
        current_revenue_m=current_revenue_m,
        net_new_required_m=net_new,
        rationale=matched_rationale,
    )


# ---------------------------------------------------------------------------
#  Top-level orchestrator
# ---------------------------------------------------------------------------


def assemble_business_case(
    *,
    firm_data: pd.DataFrame,
    composite_by_year: pd.DataFrame,
    section_order: list[tuple[str, str]],
    firm_short: str,
    firm_legal_name: Optional[str],
    primary_color_hex: str,
    cci_lookup: dict,
    base_year: int,
    start_year: int,
    end_year: int,
    fmi_forecast,
    assumptions: PilotAssumptions,
    research,
    forced_sector_keys: Optional[list[str]] = None,
    target_year_override: Optional[int] = None,
    pilot_volume_mid_override: Optional[int] = None,
    win_rate_uplift_mid_pp_override: Optional[float] = None,
    pilot_duration_quarters_override: Optional[int] = None,
) -> BusinessCaseInputs:
    """
    Resolve target_year (priority: explicit > research > assumptions default),
    rank sectors, pick, project, build ROI, compute active-growth target.
    """
    # 1. Resolve target_year
    target_year = target_year_override
    if target_year is None and research and getattr(research, "fmiTargets", None):
        for block in research.fmiTargets.values():
            if not isinstance(block, dict):
                continue
            for key in block.keys():
                if key.startswith("share"):
                    try:
                        candidate = int(key.replace("share", ""))
                        if target_year is None or candidate < target_year:
                            target_year = candidate
                    except ValueError:
                        continue
            if target_year is not None:
                break
    if target_year is None:
        target_year = assumptions.default_target_year

    if target_year <= end_year:
        target_year = end_year + 1

    # 2. Rank candidates (forced sectors bypass the size/share filter)
    candidates = rank_sectors(
        firm_data=firm_data,
        composite_by_year=composite_by_year,
        section_order=section_order,
        firm_short=firm_short,
        cci_lookup=cci_lookup,
        base_year=base_year,
        start_year=start_year,
        end_year=end_year,
        thresholds=assumptions.filter_thresholds,
        always_include=forced_sector_keys,
    )

    # 3. Pick sector
    pick = pick_sector(
        candidates=candidates,
        bundling_rules=assumptions.bundling_rules,
        forced_keys=forced_sector_keys,
        firm_short=firm_short,
    )

    # 4. Project market
    projection = project_target_market_size(
        composite_by_year=composite_by_year,
        sector_keys=pick.sector_keys,
        end_year=end_year,
        target_year=target_year,
        fmi_forecast=fmi_forecast,
    )

    # 5. Active-growth target
    active_growth = compute_active_growth_target(
        sector_pick=pick,
        market_projection=projection,
        research=research,
        target_year=target_year,
    )

    # 6. ROI table
    per_firm_override = None
    if research and getattr(research, "revwinPilot", None):
        per_firm_override = research.revwinPilot

    roi_table = build_roi_table(
        sector_keys=pick.sector_keys,
        assumptions=assumptions,
        per_firm_override=per_firm_override,
        pilot_volume_mid_override=pilot_volume_mid_override,
        win_rate_uplift_mid_pp_override=win_rate_uplift_mid_pp_override,
        pilot_duration_quarters_override=pilot_duration_quarters_override,
    )

    return BusinessCaseInputs(
        firm_short=firm_short,
        firm_legal_name=firm_legal_name,
        end_year=end_year,
        target_year=target_year,
        primary_color_hex=primary_color_hex,
        sector_pick=pick,
        market_projection=projection,
        roi_table=roi_table,
        active_growth=active_growth,
        pilot_duration_quarters=roi_table.pilot_duration_quarters,
        research=research,
    )


if __name__ == "__main__":
    # Smoke test (requires data dir set up). Run from repo root:
    #   python lib/business_case.py
    import sys
    HERE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(HERE / "lib"))
    from ingest import build_panel, load_cci_annual
    from resolve import get_firm_panel, resolve as resolve_fn
    from charts import build_composite_by_year
    from forecast import load_fmi_forecast
    from research import load_research
    from docx_render import SECTION_ORDER, SECTOR_PRIMARY_HEX

    panel = build_panel(HERE / "data" / "enr")
    cci = load_cci_annual(HERE / "data" / "cci.xlsx", base_year=2025)
    cci_lookup = dict(zip(cci["year"], cci["deflator"]))
    composite = build_composite_by_year(panel)
    fmi = load_fmi_forecast(HERE / "data" / "fmi_forecast.json")

    match = resolve_fn(panel, "HDR", interactive=False)
    firm_data = get_firm_panel(panel, match)
    firm_short = match.firm_keys[0] if match.firm_keys else "HDR"
    research = load_research(HERE / "data" / "research" / f"{firm_short}.json")
    assumptions = load_pilot_assumptions(HERE / "data" / "revwin_pilot_assumptions.json")

    bc = assemble_business_case(
        firm_data=firm_data, composite_by_year=composite,
        section_order=SECTION_ORDER, firm_short=firm_short,
        firm_legal_name=(research.firmLegalName if research else None),
        primary_color_hex=(research.primaryColorHex if research and research.primaryColorHex else SECTOR_PRIMARY_HEX["total"]),
        cci_lookup=cci_lookup, base_year=2025,
        start_year=2005, end_year=2025,
        fmi_forecast=fmi, assumptions=assumptions, research=research,
        forced_sector_keys=None,
    )
    print(json.dumps(bc.to_facts_dict(), indent=2, default=str))
