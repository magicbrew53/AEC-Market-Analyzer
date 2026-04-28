"""
Firm resolver: turn a user-typed firm name into a canonical firm_key (or set of
firm_keys for M&A-continuous histories) in the ENR panel.

Three layers, in order:

  1. ALIAS_FILE: a JSON file shipped with the script that maps user input variants
     to canonical firm_keys. Includes M&A continuities (e.g., "AECOM" merges
     "AECOM TECHNOLOGY" 2006-2014 + "AECOM" 2015-2026).

  2. EXACT MATCH on firm_key (uppercased + normalized version of user input).

  3. FUZZY MATCH using rapidfuzz. Top candidates are presented to the user with
     rank/revenue/year coverage; user picks one. Selection is then written back
     to a USER_CACHE file so subsequent runs are silent.

The output is a list of firm_keys whose rows should be UNIONED to form the
firm's 21-year history. For most firms it's a single key; for renamed firms
it's multiple.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ingest import _normalize_firm_key  # noqa


# ---------- Built-in alias map ----------

# Each entry maps a user-friendly name to the list of firm_keys to UNION.
# When a firm has been renamed, multiple keys are unioned (these are continuous
# histories of the SAME firm). When a firm has merged with/acquired another, the
# acquirer's key alone is used — DO NOT union, that would double-count revenue
# in the years both firms appeared independently.
#
# Convention: lowercase keys; values are lists of canonical firm_keys.
BUILTIN_ALIASES: dict[str, list[str]] = {
    # Common shortcuts (single key, just for ease of typing)
    "jacobs": ["JACOBS", "JACOBS SOLUTIONS"],
    "tetra tech": ["TETRA TECH"],
    "wsp": ["WSP", "WSP USA", "WSP GROUP", "WSP PARSONS BRINCKERHOFF"],
    "fluor": ["FLUOR"],
    "hdr": ["HDR"],
    "burns & mcdonnell": ["BURNS & MCDONNELL"],
    "burns and mcdonnell": ["BURNS & MCDONNELL"],
    "stantec": ["STANTEC"],
    "hntb": ["HNTB COS"],
    "arcadis": [
        "ARCADIS", "ARCADIS PINNACLEONE", "ARCADIS US", "ARCADIS U S",
        "ARCADIS MALCOLM PIRNIE", "ARCADIS MALCOLM PIRNIE RTKL",
        "ARCADIS U S RTKL RISE", "ARCADIS U S RTKL", "ARCADIS U S RTKL CALLISON",
        "ARCADIS NORTH AMERICA CALLISONRTKL", "ARCADIS NORTH AMERICA CALLISON RTKL",
        "ARCADIS NORTH AMERICA",
    ],
    "kiewit": ["KIEWIT"],
    "gensler": ["GENSLER"],
    "black & veatch": ["BLACK & VEATCH"],
    "parsons": ["PARSONS"],
    "trc": ["TRC COS", "TRC"],
    "terracon": ["TERRACON CONSULTANTS", "TERRACON"],
    "bechtel": ["BECHTEL"],
    "leidos": ["LEIDOS"],
    "sargent & lundy": ["SARGENT & LUNDY"],
    "cdm smith": ["CDM SMITH", "CDM"],
    "colliers": ["COLLIERS ENGINEERING & DESIGN", "MASER CONSULTING"],
    "exp": ["EXP", "EXP GLOBAL", "EXP US SERVICES"],
    "nv5": ["NV5 GLOBAL", "NV5 HOLDINGS", "NV5", "NOLTE VERTICAL 5 NV5"],
    "michael baker": ["MICHAEL BAKER INTERNATIONAL", "MICHAEL BAKER"],
    "tylin": ["TYLIN", "T.Y. LIN INTERNATIONAL"],
    "ty lin": ["TYLIN", "T.Y. LIN INTERNATIONAL"],
    "kleinfelder": ["KLEINFELDER", "KLEINFELDER GROUP"],
    "kimley-horn": ["KIMLEY HORN", "KIMLEY HORN AND ASSOCIATES"],
    "kimley horn": ["KIMLEY HORN", "KIMLEY HORN AND ASSOCIATES"],
    "scs": ["SCS ENGINEERS"],
    "scs engineers": ["SCS ENGINEERS"],
    "wood": ["WOOD"],
    "weston": ["WESTON SOLUTIONS"],
    "geosyntec": ["GEOSYNTEC CONSULTANTS"],
    "psomas": ["PSOMAS"],
    "rs&h": ["RS&H"],
    "stv": ["STV"],
    "ardurra": ["ARDURRA"],
    "freese and nichols": ["FREESE AND NICHOLS"],
    "wade trim": ["WADE TRIM"],
    "mead & hunt": ["MEAD & HUNT"],
    "walter p moore": ["WALTER P MOORE"],
    "hga": ["HGA"],
    "perkins eastman": ["PERKINS EASTMAN"],
    "perkins&will": ["PERKINS & WILL", "PERKINS+WILL"],
    "perkins and will": ["PERKINS & WILL", "PERKINS+WILL"],
    "som": ["SKIDMORE OWINGS & MERRILL", "SOM"],
    "skidmore": ["SKIDMORE OWINGS & MERRILL", "SOM"],
    "page": ["PAGE"],
    "syska": ["SYSKA HENNESSY"],
    "ghd": ["GHD"],
    # AtkinsRealis (2024+) was SNC-Lavalin (2018-2023) which acquired WS Atkins in 2017.
    # ATKINS NORTH AMERICA (2010-2017) was the US subsidiary of UK-based WS Atkins; we
    # do NOT include it here because mixing it would conflate a 2017 M&A event with
    # organic growth. Use --research-file to override and include the Atkins history
    # for a particular pursuit if Don wants it.
    "atkinsrealis": ["ATKINSREALIS", "SNC LAVALIN", "SNC LAVALIN GROUP"],
    "snc-lavalin": ["ATKINSREALIS", "SNC LAVALIN", "SNC LAVALIN GROUP"],
    "snc lavalin": ["ATKINSREALIS", "SNC LAVALIN", "SNC LAVALIN GROUP"],
    "atkins": ["ATKINS NORTH AMERICA"],  # historical-only; deliberately separate
    "mwh": ["MWH GLOBAL", "MWH"],
    "ch2m": ["CH2M", "CH2M HILL"],
    "aecom": ["AECOM", "AECOM TECHNOLOGY"],
    # Note: do NOT alias "urs" → ["URS", "AECOM"] because URS was acquired by AECOM in 2014.
    # Treat URS as historical-only.
    "urs": ["URS"],
}


@dataclass
class FirmMatch:
    """A resolved firm: one or more firm_keys whose rows should be unioned."""

    display_name: str  # e.g., "AECOM"
    firm_keys: list[str]  # e.g., ["AECOM", "AECOM TECHNOLOGY"]
    n_editions: int  # how many edition_years are covered after union
    latest_rank: int | None  # most recent rank
    latest_revenue: float | None  # most recent total_rev_m
    latest_edition: int | None


def _summarize_keys(panel: pd.DataFrame, firm_keys: list[str]) -> dict:
    """Compute summary stats for a set of firm_keys (treated as one firm)."""
    rows = panel[panel["firm_key"].isin(firm_keys)]
    if rows.empty:
        return {"n_editions": 0, "latest_rank": None, "latest_revenue": None, "latest_edition": None}
    # Latest = highest edition_year. If multiple keys hit the same year, take
    # the one with highest revenue (handles edge cases like rename year overlap).
    latest = rows.sort_values(["edition_year", "total_rev_m"], ascending=[False, False]).iloc[0]
    return {
        "n_editions": rows["edition_year"].nunique(),
        "latest_rank": int(latest["rank"]) if pd.notna(latest["rank"]) else None,
        "latest_revenue": float(latest["total_rev_m"]) if pd.notna(latest["total_rev_m"]) else None,
        "latest_edition": int(latest["edition_year"]),
    }


def _validate_keys_exist(panel: pd.DataFrame, firm_keys: list[str]) -> list[str]:
    """Drop any keys that aren't actually in the panel."""
    available = set(panel["firm_key"].unique())
    return [k for k in firm_keys if k in available]


def _try_alias(panel: pd.DataFrame, user_input: str, alias_map: dict) -> FirmMatch | None:
    """Look up user_input in the alias map (case-insensitive, whitespace-collapsed)."""
    norm = " ".join(user_input.strip().lower().split())
    if norm in alias_map:
        keys = _validate_keys_exist(panel, alias_map[norm])
        if not keys:
            return None
        summary = _summarize_keys(panel, keys)
        # Display name: use the canonical raw form from latest edition
        latest_rows = panel[panel["firm_key"].isin(keys)].sort_values(
            ["edition_year", "total_rev_m"], ascending=[False, False]
        )
        display = latest_rows.iloc[0]["firm_raw"] if not latest_rows.empty else user_input
        return FirmMatch(
            display_name=display,
            firm_keys=keys,
            **summary,
        )
    return None


def _try_exact(panel: pd.DataFrame, user_input: str) -> FirmMatch | None:
    """Exact match on the normalized firm_key."""
    target = _normalize_firm_key(user_input)
    if target and target in set(panel["firm_key"].unique()):
        summary = _summarize_keys(panel, [target])
        latest_rows = panel[panel["firm_key"] == target].sort_values("edition_year", ascending=False)
        display = latest_rows.iloc[0]["firm_raw"]
        return FirmMatch(display_name=display, firm_keys=[target], **summary)
    return None


def _fuzzy_candidates(panel: pd.DataFrame, user_input: str, top_n: int = 8) -> list[FirmMatch]:
    """Return top-N fuzzy matches by similarity to the user's input."""
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        # Fall back to simple substring match if rapidfuzz isn't available.
        target = user_input.upper().strip()
        unique_keys = panel["firm_key"].unique()
        candidates = sorted(
            (k for k in unique_keys if target in k or k in target),
            key=lambda k: -panel[panel["firm_key"] == k]["total_rev_m"].max(),
        )[:top_n]
    else:
        target = _normalize_firm_key(user_input)
        unique_keys = list(panel["firm_key"].unique())
        scored = process.extract(
            target, unique_keys, scorer=fuzz.WRatio, limit=top_n * 2
        )
        # scored is list of (key, score, idx). Filter weak matches (<60).
        candidates = [k for k, score, _ in scored if score >= 60][:top_n]

    out: list[FirmMatch] = []
    for k in candidates:
        summary = _summarize_keys(panel, [k])
        rows = panel[panel["firm_key"] == k].sort_values("edition_year", ascending=False)
        display = rows.iloc[0]["firm_raw"] if not rows.empty else k
        out.append(FirmMatch(display_name=display, firm_keys=[k], **summary))
    return out


# ---------- User cache ----------


def _load_user_cache(cache_path: Path) -> dict[str, list[str]]:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_user_cache(cache_path: Path, cache: dict[str, list[str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True))


# ---------- Main entry ----------


def resolve(
    panel: pd.DataFrame,
    user_input: str,
    user_cache_path: Path | None = None,
    interactive: bool = True,
    alias_overrides: dict[str, list[str]] | None = None,
) -> FirmMatch:
    """
    Resolve a user-typed firm name to a FirmMatch.

    Priority:
      1. user cache (alias file populated by prior interactive runs)
      2. built-in aliases
      3. exact firm_key match
      4. fuzzy match → interactive prompt (if interactive=True) → write to user cache

    Raises:
      ValueError if no match found and interactive=False (or user gives up)
    """
    aliases = dict(BUILTIN_ALIASES)
    if alias_overrides:
        aliases.update(alias_overrides)

    # 1. User cache
    user_cache: dict[str, list[str]] = {}
    if user_cache_path:
        user_cache = _load_user_cache(user_cache_path)
        norm = " ".join(user_input.strip().lower().split())
        if norm in user_cache:
            keys = _validate_keys_exist(panel, user_cache[norm])
            if keys:
                summary = _summarize_keys(panel, keys)
                rows = panel[panel["firm_key"].isin(keys)].sort_values(
                    ["edition_year", "total_rev_m"], ascending=[False, False]
                )
                display = rows.iloc[0]["firm_raw"] if not rows.empty else user_input
                return FirmMatch(display_name=display, firm_keys=keys, **summary)

    # 2. Built-in alias
    m = _try_alias(panel, user_input, aliases)
    if m:
        return m

    # 3. Exact match
    m = _try_exact(panel, user_input)
    if m:
        return m

    # 4. Fuzzy + interactive
    candidates = _fuzzy_candidates(panel, user_input, top_n=8)
    if not candidates:
        raise ValueError(f"No matches for '{user_input}'.")

    if not interactive:
        # Best-effort: return top fuzzy match
        return candidates[0]

    print(f"\nNo exact match for '{user_input}'. Top candidates:\n")
    for i, c in enumerate(candidates, 1):
        rev_str = f"${c.latest_revenue/1000:.2f}B" if c.latest_revenue and c.latest_revenue > 1000 else (
            f"${c.latest_revenue:.0f}M" if c.latest_revenue else "—"
        )
        rank_str = f"#{c.latest_rank}" if c.latest_rank else "—"
        print(
            f"  ({i}) {c.display_name:<40} rank {rank_str:<5} {rev_str:<10} "
            f"{c.n_editions} editions (latest: ENR {c.latest_edition})"
        )
    print(f"  ({len(candidates)+1}) None of these — abort\n")

    while True:
        choice = input("Pick one (1-{}): ".format(len(candidates) + 1)).strip()
        try:
            idx = int(choice)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= idx <= len(candidates):
            chosen = candidates[idx - 1]
            # Save to user cache
            if user_cache_path:
                norm = " ".join(user_input.strip().lower().split())
                user_cache[norm] = chosen.firm_keys
                _save_user_cache(user_cache_path, user_cache)
                print(f"  ✓ Cached '{user_input}' → {chosen.firm_keys}")
            return chosen
        elif idx == len(candidates) + 1:
            raise ValueError(f"User aborted resolution for '{user_input}'.")
        else:
            print(f"Please enter 1-{len(candidates)+1}.")


def get_firm_panel(panel: pd.DataFrame, match: FirmMatch) -> pd.DataFrame:
    """
    Return the firm's 21-year time series, merged across all firm_keys in the match.

    For union cases (renamed firms), if multiple keys hit the same edition_year
    (shouldn't happen for clean renames, but defensive), keep the row with
    higher total_rev_m.
    """
    rows = panel[panel["firm_key"].isin(match.firm_keys)].copy()
    rows = rows.sort_values(["data_year", "total_rev_m"], ascending=[True, False])
    rows = rows.drop_duplicates(subset=["data_year"], keep="first").reset_index(drop=True)
    return rows


if __name__ == "__main__":
    # Smoke test
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ingest import build_panel

    here = Path(__file__).resolve().parent.parent
    panel = build_panel(here / "data" / "enr")

    for test_input in ["HDR", "AECOM", "Jacobs", "ch2m", "Stantec", "kimley horn"]:
        match = resolve(panel, test_input, interactive=False)
        firm_data = get_firm_panel(panel, match)
        print(
            f"{test_input:<15} → keys={match.firm_keys}, "
            f"{len(firm_data)} years of data ({firm_data['data_year'].min()}-{firm_data['data_year'].max()})"
        )
