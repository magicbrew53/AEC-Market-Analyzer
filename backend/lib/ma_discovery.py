"""
ma_discovery.py — discover and verify M&A history for a parent firm.

A two-stage pipeline:

  1. discover_acquisitions(...) — calls Claude Sonnet with the web_search
     tool to list acquisitions where the acquired firm appeared on ENR
     Top 500. Returns AcquisitionCandidate rows with source URLs.

     (Originally Haiku; switched to Sonnet because Haiku consistently
     stopped at 1-3 obvious hits even with strong prompt + research
     strategy. Sonnet's multi-step reasoning produces a much more
     complete list. Cost is ~$0.10-0.15 per fresh run.)

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

List EVERY material acquisition {firm_short} completed between {min_year} and {max_year} where the acquired firm was a design, engineering, architecture, or environmental-consulting firm that appeared on an ENR Top 500 Design Firms ranking at any point before being acquired.

INCLUDE:
  - Design firms (architecture, engineering, A/E firms)
  - Specialty engineering firms (transportation, water, environmental, power)
  - Environmental consulting firms
  - Cost-consulting / program-management firms with design practices
  - Firms that historically appeared on the ENR Top 500 Design Firms list

EXCLUDE:
  - Pure construction firms / general contractors (they're on the ENR Top 400 Contractors list, not Top 500 Design Firms)
  - Construction-management-only firms with no design practice
  - Small tuck-in acquisitions of practices that were never independently ranked
  - Acquisitions older than {min_year} or later than {max_year}

RESEARCH STRATEGY — be thorough:
  1. Search "{firm_short} acquisitions" and "{firm_short} list of mergers and acquisitions".
  2. Check Wikipedia for {firm_short} and look at the article's "Acquisitions" or "History" section. Wikipedia is usually the most comprehensive single source.
  3. Search for "{firm_short} acquires" press-release archives.
  4. For each acquisition you find, do a second search to verify the year and confirm the acquired firm was a design / engineering / architecture / environmental-consulting firm (not a pure contractor).
  5. Be inclusive at this stage — return any acquisition where the acquired firm did engineering or design work, even if you're not 100% sure it appeared on ENR Top 500. A downstream verifier will check against the ENR panel. It's better to over-report than miss real acquisitions.
  6. Don't stop at the first 2-3 hits — large AEC firms routinely have 5-15 design-firm acquisitions over a 20-year span. Keep searching until you've exhausted the Wikipedia/press-release sources.
  7. You may narrate your research process between searches if helpful — the parser is tolerant of interleaved prose. But your FINAL message must contain ONLY the JSON array, no other text.

OUTPUT FORMAT — STRICT, NON-NEGOTIABLE:
After you finish all research and verification, your FINAL message must contain ONLY the JSON array — nothing else. No introduction ("Here are the acquisitions"), no headers, no markdown fences, no closing commentary. The first character of your final message must be `[` and the last character must be `]`. If you write any prose in the final message you have failed the task.

Each entry has exactly these keys:

  "acquired_firm":     <official name as commonly reported>,
  "acquisition_year":  <int>,
  "source_url":        <url that cites this acquisition>,
  "confidence":        "high" | "medium" | "low"

Cite a real source URL for each — Wikipedia, the firm's press release, ENR coverage, or M&A news outlets. If you cannot find a source URL, OMIT that acquisition entirely. Do not invent years. If the year is uncertain, mark "low" confidence and provide your best public estimate.

If you find no qualifying acquisitions, return an empty array: []
"""


def _build_discovery_prompt(firm_short: str, min_year: int, max_year: int) -> str:
    return _DISCOVERY_PROMPT.format(
        firm_short=firm_short, min_year=min_year, max_year=max_year,
    )


def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of the model response.

    Tolerant of:
      - Direct JSON output
      - JSON inside ```json fences
      - JSON embedded in narration / interleaved research prose (Sonnet
        often emits multi-step thinking text before the final JSON)

    Strategy: try fast paths first (direct parse, code fence), then scan
    the whole text for every `[...]` substring and return the LARGEST one
    that parses as a JSON list of objects.
    """
    text = text.strip()

    # 1. Direct parse
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return v
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence_match:
        try:
            v = json.loads(fence_match.group(1))
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass

    # 3. Bracket-balanced scan: find every top-level [...] block, try each.
    candidates: list[list] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                blob = text[start:i + 1]
                try:
                    v = json.loads(blob)
                    if isinstance(v, list):
                        candidates.append(v)
                except json.JSONDecodeError:
                    pass
                start = -1

    if candidates:
        # Prefer the array with the most items, then the longest serialized form
        # — that's almost always the final result, not an intermediate example.
        candidates.sort(
            key=lambda arr: (len(arr), sum(1 for x in arr if isinstance(x, dict))),
            reverse=True,
        )
        return candidates[0]

    raise ValueError(
        f"Could not parse JSON array from model response. First 800 chars:\n{text[:800]}"
    )


def discover_acquisitions(
    firm_short: str,
    panel: pd.DataFrame,
    *,
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
    min_year: int = 2005,
    max_year: Optional[int] = None,
    max_tokens: int = 24000,
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

    # Streaming is required for any call that may run >10 minutes — the
    # combination of web_search round-trips + max_tokens=24000 can exceed
    # that, so we always stream. We don't need the intermediate events,
    # only the final message.
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        final_message = stream.get_final_message()

    # The model may emit multiple content blocks (server-tool results,
    # interleaved with text). Concatenate the text blocks for parsing.
    text_parts: list[str] = []
    for block in final_message.content:
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
