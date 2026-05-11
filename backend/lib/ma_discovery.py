"""
ma_discovery.py — discover and verify M&A history for a parent firm.

A two-stage pipeline:

  1. discover_acquisitions(...) — calls Claude Haiku with the web_search tool
     to list acquisitions where the acquired firm appeared on ENR Top 500.
     Returns AcquisitionCandidate rows with source URLs.

  2. verify_acquisitions(...) — runs each candidate through the existing
     firm resolver (lib/resolve.py) and confirms the acquired firm has ENR
     panel coverage BEFORE the claimed acquisition year and (mostly) absent
     AFTER. Drops candidates that don't resolve; flags ambiguous ones as
     needs_review.

Results are cached per-firm at `data/ma_cache/{FIRM_SHORT}.json` so the
LLM call only runs once per firm (until --refresh).

This module is the data layer for the Roll-up M&A History feature. It does
no chart rendering and no UI work — those live in Layers 2 and 3.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
import pandas as pd

from resolve import FirmMatch, get_firm_panel, resolve as resolve_fn


# ---------------------------------------------------------------------------
#  Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionCandidate:
    acquired_firm: str
    acquisition_year: int
    source_url: str
    confidence: str  # "high" | "medium" | "low"


@dataclass
class VerifiedAcquisition:
    acquired_firm_display: str
    firm_keys: list[str]          # one or more — handles renames within the acquired firm's own history
    acquisition_year: int
    last_pre_merger_year: int
    source_url: str
    confidence: str
    needs_review: bool
    enabled: bool = True          # user-toggleable; defaults to true


@dataclass
class MARollupConfig:
    parent_firm_short: str
    acquisitions: list[VerifiedAcquisition]
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
#  Discovery (Haiku + web_search)
# ---------------------------------------------------------------------------


_DISCOVERY_PROMPT = """You are researching publicly-known acquisitions made by the AEC firm "{firm_short}".

List every acquisition {firm_short} has made between {min_year} and {max_year} where the acquired firm appeared on the ENR Top 500 Design Firms list at any point before being acquired.

CRITICAL CONSTRAINTS:
  - Only include acquisitions where the acquired firm appeared on the ENR Top 500 Design Firms list.
  - Do NOT include small tuck-in acquisitions that never appeared on ENR Top 500.
  - Cite a source URL for each acquisition — Wikipedia, the firm's press release, ENR coverage, or M&A news outlets.
  - If you cannot find a source URL for an acquisition, OMIT it entirely.
  - Do not invent or estimate years. If the year is uncertain, mark "low" confidence and include the best public estimate.
  - Use the search tool aggressively. Search for "{firm_short} acquisitions", then for specific acquired-firm names you find.

Output ONLY a JSON array, no prose, no markdown fences, no explanation. Each entry has exactly these keys:

  "acquired_firm":     <official name as commonly reported>,
  "acquisition_year":  <int>,
  "source_url":        <url citing the acquisition>,
  "confidence":        "high" | "medium" | "low"

If you find no qualifying acquisitions, return an empty array: []
"""


def _build_discovery_prompt(firm_short: str, min_year: int, max_year: int) -> str:
    return _DISCOVERY_PROMPT.format(
        firm_short=firm_short, min_year=min_year, max_year=max_year,
    )


def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of the model response. Tolerant of code fences or trailing prose."""
    text = text.strip()
    # Direct parse
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return v
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence_match:
        try:
            v = json.loads(fence_match.group(1))
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass

    # Find a top-level bracketed array
    bracket_match = re.search(r"\[\s*(?:\{[\s\S]*?\}\s*,?\s*)*\]", text)
    if bracket_match:
        try:
            v = json.loads(bracket_match.group(0))
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse JSON array from model response. First 500 chars:\n{text[:500]}"
    )


def discover_acquisitions(
    firm_short: str,
    panel: pd.DataFrame,
    *,
    api_key: Optional[str] = None,
    model: str = "claude-haiku-4-5",
    min_year: int = 2005,
    max_year: Optional[int] = None,
    max_tokens: int = 4000,
) -> list[AcquisitionCandidate]:
    """
    Call Claude Haiku with the web_search tool to list acquisitions for
    `firm_short`. Returns raw candidates (NOT verified against the panel
    yet — call verify_acquisitions() after).

    The `panel` arg is currently unused by discovery itself but accepted so
    callers can chain discovery → verification without re-importing pandas.

    Raises RuntimeError if ANTHROPIC_API_KEY is not set.
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key. Set ANTHROPIC_API_KEY environment variable. "
            "M&A discovery requires Haiku + web_search and cannot run without it."
        )

    if max_year is None:
        max_year = datetime.now().year

    prompt = _build_discovery_prompt(firm_short, min_year, max_year)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # The model may emit multiple content blocks (server-tool results,
    # interleaved with text). Concatenate the text blocks for parsing.
    text_parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
    raw_text = "\n".join(text_parts).strip()

    if not raw_text:
        return []

    items = _extract_json_array(raw_text)

    out: list[AcquisitionCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        acquired_firm = (item.get("acquired_firm") or "").strip()
        try:
            year = int(item.get("acquisition_year"))
        except (TypeError, ValueError):
            continue
        source_url = (item.get("source_url") or "").strip()
        confidence = (item.get("confidence") or "").strip().lower()

        if not acquired_firm or not source_url:
            continue
        if confidence not in ("high", "medium", "low"):
            confidence = "low"
        if confidence == "low":
            # Spec: reject "low" confidence entries.
            continue

        out.append(AcquisitionCandidate(
            acquired_firm=acquired_firm,
            acquisition_year=year,
            source_url=source_url,
            confidence=confidence,
        ))
    return out


# ---------------------------------------------------------------------------
#  Verification
# ---------------------------------------------------------------------------


def verify_acquisitions(
    candidates: list[AcquisitionCandidate],
    panel: pd.DataFrame,
    *,
    user_cache_path: Optional[Path] = None,
    alias_overrides: Optional[dict[str, list[str]]] = None,
) -> list[VerifiedAcquisition]:
    """
    For each candidate:
      1. Resolve the acquired_firm name via lib/resolve.py (non-interactive,
         takes the top fuzzy match). Drop if no match.
      2. Confirm panel coverage in editions before the claimed acquisition
         year. Drop if no pre-merger rows.
      3. Confirm the acquired firm largely DISAPPEARS after acquisition_year+1.
         If it still appears, keep the candidate but flag needs_review=True.
    """
    verified: list[VerifiedAcquisition] = []
    for cand in candidates:
        # 1. Resolve
        try:
            match: FirmMatch = resolve_fn(
                panel,
                cand.acquired_firm,
                user_cache_path=user_cache_path,
                interactive=False,
                alias_overrides=alias_overrides,
            )
        except (ValueError, Exception):
            continue

        # If the resolver dropped onto the SAME parent firm (e.g., name overlap),
        # that's not an acquisition. Skip.
        if not match.firm_keys:
            continue

        firm_data = get_firm_panel(panel, match)
        if firm_data.empty:
            continue

        pre_merger = firm_data[firm_data["data_year"] < cand.acquisition_year]
        if pre_merger.empty:
            continue

        post_merger = firm_data[firm_data["data_year"] > cand.acquisition_year + 1]
        needs_review = not post_merger.empty
        last_pre = int(pre_merger["data_year"].max())

        verified.append(VerifiedAcquisition(
            acquired_firm_display=match.display_name,
            firm_keys=list(match.firm_keys),
            acquisition_year=cand.acquisition_year,
            last_pre_merger_year=last_pre,
            source_url=cand.source_url,
            confidence=cand.confidence,
            needs_review=needs_review,
            enabled=True,
        ))

    return verified


# ---------------------------------------------------------------------------
#  Cache I/O
# ---------------------------------------------------------------------------


def _cache_path(firm_short: str, cache_dir: Path) -> Path:
    return Path(cache_dir) / f"{firm_short}.json"


def load_ma_cache(firm_short: str, cache_dir: Path) -> Optional[MARollupConfig]:
    """Return the cached MARollupConfig for a firm, or None if absent / malformed."""
    path = _cache_path(firm_short, cache_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    acqs = []
    for a in raw.get("acquisitions", []):
        try:
            acqs.append(VerifiedAcquisition(
                acquired_firm_display=a["acquired_firm_display"],
                firm_keys=list(a.get("firm_keys") or []),
                acquisition_year=int(a["acquisition_year"]),
                last_pre_merger_year=int(a.get("last_pre_merger_year", a["acquisition_year"] - 1)),
                source_url=a.get("source_url", ""),
                confidence=a.get("confidence", "medium"),
                needs_review=bool(a.get("needs_review", False)),
                enabled=bool(a.get("enabled", True)),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    cached_at_str = raw.get("cached_at")
    try:
        cached_at = datetime.fromisoformat(cached_at_str) if cached_at_str else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        cached_at = datetime.now(timezone.utc)

    return MARollupConfig(
        parent_firm_short=raw.get("parent_firm_short", firm_short),
        acquisitions=acqs,
        cached_at=cached_at,
    )


def save_ma_cache(config: MARollupConfig, cache_dir: Path) -> Path:
    """Write a MARollupConfig to disk and return the resulting path."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(config.parent_firm_short, cache_dir)
    out = {
        "parent_firm_short": config.parent_firm_short,
        "cached_at": config.cached_at.isoformat(),
        "acquisitions": [asdict(a) for a in config.acquisitions],
    }
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
#  CLI helpers (used by revwin_report.py --discover-ma)
# ---------------------------------------------------------------------------


def print_acquisitions_table(config: MARollupConfig) -> None:
    """Pretty-print the verified list for the CLI."""
    if not config.acquisitions:
        print(f"  (no qualifying acquisitions found for {config.parent_firm_short})")
        return
    print()
    print(f"  {'Year':<6} {'Acquired firm':<35} {'On?':<5} {'Conf':<7} {'Review?':<8} Source")
    print(f"  {'-'*6} {'-'*35} {'-'*5} {'-'*7} {'-'*8} {'-'*30}")
    for a in sorted(config.acquisitions, key=lambda x: x.acquisition_year):
        flag_on = "yes" if a.enabled else "no"
        flag_review = "yes" if a.needs_review else ""
        print(
            f"  {a.acquisition_year:<6} {a.acquired_firm_display[:35]:<35} "
            f"{flag_on:<5} {a.confidence:<7} {flag_review:<8} {a.source_url}"
        )
    print()
