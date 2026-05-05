"""
narrative.py — generates section narratives via the Anthropic API.

The LLM is given:
  - A strict instruction template
  - The facts object (numeric ground truth) for the section
  - The chart titles available (so it can refer to them in passing)
  - Optional: prior research notes (firm profile, M&A history, FMI forecasts)

It is told to:
  - Cite ONLY numbers present in the facts object
  - Never invent specific dollar values, percentages, or years
  - Match Don Sherman's voice: analytical, plain-spoken, no hype, no jargon-as-drama
  - Produce 2-4 paragraphs, ~300-500 words, in markdown
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Optional

import anthropic

from compute import SectionFacts


SECTOR_NARRATIVE_PROMPT = """You are writing one section of a quantitative market analysis report \
for an Architecture / Engineering / Construction (AEC) firm. The author of the canonical reference \
report (Don Sherman, AEC Market Masters) has a specific analytical voice: plain-spoken, numerically \
precise, never hype, no metaphors, no consultant jargon, and never overstates. Your job is to \
produce the prose for the **{sector_label}** section of the report on **{firm_short}**.

You will be given a JSON `facts` object with every number you are allowed to use. **Cite only \
numbers from this object.** Do not invent specific dollar amounts, percentages, ranks, years, \
or named drivers (regulations, projects, deal names, hires) — if a driver is not in the facts, \
do not name one. If you wish to attribute outperformance to "execution" or "competitive capture" \
you may, but do not name specific drivers.

When the facts include both nominal and real (CCI-adjusted) figures, prefer real figures for \
multi-year growth comparisons and nominal figures for current absolute scale. Cumulative \
nominal growth percentages are dramatic but partly reflect inflation; the real CAGR is the \
genuine performance signal.

Format requirements:
  - 2 to 4 paragraphs, 200-450 words total
  - Markdown, no headings (the section heading is added separately)
  - First paragraph: opening claim about the firm's position in this sector — outperformer, \
average, lagging, non-participant — anchored to the facts
  - Subsequent paragraphs: support the opening claim with specific cited numbers; cover \
market share trajectory, sector contribution to firm total (if applicable), and any notable \
counter-cyclical or peak/trough years from the facts
  - Round dollars to the level of precision used in `facts` ($M for sector revenue, $B for \
composite). Use {firm_short}'s convention.
  - When citing percent growth, write percentages with one decimal (e.g., "13.4%"), not \
two ("13.40%"). Whole-number percentages don't need the decimal.
  - Never say "the data shows" or "as you can see in the chart" — write as a direct \
analytical observation
  - Avoid phrases like "robust", "impressive", "strong" without quantification

Special handling:
  - If `start_nom_m` is missing or near-zero (firm did not participate in this sector at the \
start), explicitly note that as the opening claim ("[firm] entered the sector after [year]" \
or "[firm] does not meaningfully participate in [sector]"). Do not fabricate trajectory.
  - If `end_pct_of_firm_total` is below 2%, this is a peripheral sector — say so plainly
  - If `firm_2009_premium` is large positive (>0.20 = 20 points) AND firm_2009_real_yoy > 0 \
AND comp_2009_real_yoy < 0, you may call out the 2009 counter-cyclical performance using \
the actual numbers in facts
  - Do NOT cite "+28.3%" or any other number unless it appears in `facts`
  - If `firm_2009_real_yoy` is unusually large (>0.50 = 50%), you may quote it but flag that \
the magnitude is partly driven by sector-specific events (without naming them)

Here are the facts:

```json
{facts_json}
```

Now write the narrative for the {sector_label} section. Output the prose only — no preamble, \
no postscript, no markdown fences.
"""


def render_sector_narrative(
    facts: SectionFacts,
    *,
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1500,
) -> str:
    """
    Generate prose for one section. Returns markdown.

    Args:
        facts: structured facts from compute.compute_section_facts()
        api_key: Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.
        model: model name to use
        max_tokens: max output tokens
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key. Set ANTHROPIC_API_KEY environment variable, "
            "or pass api_key=... explicitly."
        )

    facts_dict = facts.to_dict()
    # Drop the per-year rows from the prompt to keep it compact; the LLM doesn't need every row
    # to write the narrative, only the summary stats. The rows go into the data table separately.
    facts_for_prompt = {k: v for k, v in facts_dict.items() if k != "rows"}

    prompt = SECTOR_NARRATIVE_PROMPT.format(
        firm_short=facts.firm_short,
        sector_label=facts.sector_label,
        facts_json=json.dumps(facts_for_prompt, indent=2, default=str),
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    # Pull text content out
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


# =====================================================================
#  Cross-section narratives: executive summary, firm profile, etc.
# =====================================================================


EXEC_SUMMARY_PROMPT = """You are writing the **Executive Summary > Key Findings** section \
of a market analysis report on **{firm_short}**. The summary lists 6 to 10 key findings as \
markdown bullets. Each bullet:
  - Begins with **a short bold lead phrase** followed by a period
  - Then 2-3 sentences of supporting detail with cited numbers
  - Cites only numbers from the facts JSON below
  - Does not invent project names, regulations, executive names, or specific drivers

Cover (when supported by facts): total nominal and real growth across the span; ENR ranking \
trajectory including best-ever rank; market-share change; the firm's standout outperforming sector \
(largest cagr_premium_pct); the firm's largest sector by current revenue; counter-cyclical 2009 \
performance if it appears in the data; any sectors where the firm does not meaningfully participate \
(end_pct_of_firm_total < 0.02). Write in Don Sherman's voice: plain-spoken, analytical, never \
hype, never overstate.

Output format: a markdown bulleted list with one bullet per finding, separated by blank lines. \
Use `**Lead phrase.**` for the bold lead in each bullet. Do not include a heading.

Facts:

```json
{facts_json}
```

Now write the bullets."""


FIRM_PROFILE_PROMPT = """You are writing the **Firm Profile > Ownership & Historical Evolution** \
section of a market analysis report on **{firm_short}**. You will be given two sources:

1. The user-provided research notes (firm_facts) — these are authoritative for biographical and \
ownership facts. **Use only this content for biographical claims.** Do not invent CEOs, dates, \
acquisition counts, or office locations.

2. The performance numbers (perf_facts) — for any claims about revenue trajectory or growth.

Format requirements:
  - 2-4 paragraphs of plain prose (no bullets, no headings)
  - Markdown
  - 200-400 words
  - Don Sherman's voice: factual, plain-spoken, no hype

If the research file is missing or sparse, output a single paragraph noting that the firm \
profile section requires Don to fill in firm-specific biographical content, and list which \
fields are missing from the research file. Do not fabricate.

firm_facts:
```json
{firm_facts_json}
```

perf_facts (high-level summary only):
```json
{perf_facts_json}
```

Now write the Ownership & Historical Evolution narrative."""


STRATEGIC_FRAMEWORK_PROMPT = """You are writing the **Strategic Growth Framework** section of \
a market analysis report on **{firm_short}**. This section describes the firm's projected \
revenue trajectory through 2029 under both a baseline ("maintain share") scenario and any \
named "active growth" scenario from the firm's strategic plan.

You are given:
  1. The firm's strategic initiative (firm_strategy) — name, launch year, public goals, FMI sector targets
  2. Per-sector projections (sector_projections) — current revenue, projected 2029 revenue under \
the baseline, and projected 2029 revenue under any active-growth target share if specified
  3. FMI source (fmi_source) — the FMI publication driving the baseline forecast

Format:
  - 3-5 paragraphs
  - Markdown
  - 300-600 words
  - Cover: addressable market expansion 2025→2029, baseline scenario, active-growth scenario \
(only if specified in firm_strategy), and the sectors where active growth is most concentrated
  - Quote only numbers from the facts; do not invent revenue targets
  - If firm_strategy.name is null, omit the active-growth scenario entirely and base the \
section on baseline projections only

firm_strategy:
```json
{firm_strategy_json}
```

sector_projections:
```json
{sector_projections_json}
```

fmi_source: "{fmi_source}"

Now write the Strategic Growth Framework section."""


CONCLUSIONS_PROMPT = """You are writing the **Conclusions** section of a market analysis report \
on **{firm_short}**. This is a 2-3 page synthesis that pulls together the key analytical themes \
from the section narratives.

Cover the following themes (only when supported by the facts; skip themes that aren't):
  1. Sustained outperformance (or underperformance) — quantified
  2. Resilience through cycles (2009 recession; pandemic-inflation 2020-2022)
  3. Strategic transformation — sector mix shifts, market share trajectory
  4. Sector standouts — best and worst performing sectors by market share gain
  5. Positioning for continued growth — the gap (if any) between current capabilities and \
top-tier performance, framed in the firm's own strategic language

Format:
  - 4-6 paragraphs
  - Markdown
  - 400-700 words
  - Don Sherman's voice: analytical, balanced, identifies remaining opportunities without \
overstating either accomplishments or gaps

You are given a roll-up of all section facts. Quote only from this object.

```json
{rollup_json}
```

Now write the Conclusions section."""


def _call_llm(prompt: str, api_key: str | None, model: str, max_tokens: int) -> str:
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key. Set ANTHROPIC_API_KEY environment variable.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(parts).strip()


def render_exec_summary_findings(
    section_facts_list: list, firm_short: str,
    api_key: str | None = None, model: str = "claude-sonnet-4-6",
    max_tokens: int = 2000,
) -> str:
    """Generate the Key Findings bullets given a list of SectionFacts (one per sector)."""
    rollup = []
    for sf in section_facts_list:
        d = sf.to_dict()
        # Compact: drop rows
        rollup.append({k: v for k, v in d.items() if k != "rows"})
    prompt = EXEC_SUMMARY_PROMPT.format(
        firm_short=firm_short,
        facts_json=json.dumps(rollup, indent=2, default=str),
    )
    return _call_llm(prompt, api_key, model, max_tokens)


def render_firm_profile_ownership(
    firm_research,  # FirmResearch dataclass from research.py
    total_facts,    # SectionFacts for "total"
    firm_short: str,
    api_key: str | None = None, model: str = "claude-sonnet-4-6",
    max_tokens: int = 1500,
) -> str:
    """Generate the Ownership & Historical Evolution narrative."""
    if firm_research is None:
        firm_facts = {"_missing": True}
    else:
        # Pass the structured research as-is
        firm_facts = {
            "atAGlance": firm_research.atAGlance,
            "ownership": firm_research.ownership,
            "acquisitions": firm_research.acquisitions,
            "strategicInitiative": (
                firm_research.strategicInitiative.__dict__
                if firm_research.strategicInitiative else None
            ),
        }
    perf = total_facts.to_dict() if total_facts else {}
    perf_summary = {k: v for k, v in perf.items() if k not in ("rows",)}
    prompt = FIRM_PROFILE_PROMPT.format(
        firm_short=firm_short,
        firm_facts_json=json.dumps(firm_facts, indent=2, default=str),
        perf_facts_json=json.dumps(perf_summary, indent=2, default=str),
    )
    return _call_llm(prompt, api_key, model, max_tokens)


def render_strategic_framework(
    firm_research, sector_projections, fmi_source: str, firm_short: str,
    api_key: str | None = None, model: str = "claude-sonnet-4-6",
    max_tokens: int = 2000,
) -> str:
    if firm_research and firm_research.strategicInitiative:
        firm_strategy = {
            "name": firm_research.strategicInitiative.name,
            "launchYear": firm_research.strategicInitiative.launchYear,
            "goals": firm_research.strategicInitiative.goals,
            "growthOrgLeaders": firm_research.strategicInitiative.growthOrgLeaders,
            "fmiTargets": firm_research.fmiTargets,
        }
    else:
        firm_strategy = {"name": None}
    prompt = STRATEGIC_FRAMEWORK_PROMPT.format(
        firm_short=firm_short,
        firm_strategy_json=json.dumps(firm_strategy, indent=2, default=str),
        sector_projections_json=json.dumps(sector_projections, indent=2, default=str),
        fmi_source=fmi_source,
    )
    return _call_llm(prompt, api_key, model, max_tokens)


def render_conclusions(
    section_facts_list: list, firm_short: str,
    api_key: str | None = None, model: str = "claude-sonnet-4-6",
    max_tokens: int = 2500,
) -> str:
    rollup = []
    for sf in section_facts_list:
        d = sf.to_dict()
        rollup.append({k: v for k, v in d.items() if k != "rows"})
    prompt = CONCLUSIONS_PROMPT.format(
        firm_short=firm_short,
        rollup_json=json.dumps(rollup, indent=2, default=str),
    )
    return _call_llm(prompt, api_key, model, max_tokens)


# =====================================================================
#  RevWin Business Case narratives — three short LLM-generated passages.
#  Each takes a BusinessCaseInputs object, builds a strict facts JSON,
#  and asks Claude for a constrained piece of prose. Voice: Don Sherman.
# =====================================================================


_BC_VOICE_GUARDRAILS = (
    "Voice: Don Sherman — analytical, plain-spoken, no hype, no metaphors, no consultant "
    "jargon. Cite ONLY numbers from the facts JSON. Do not invent specific dollar values, "
    "percentages, named drivers (regulations, projects, deal names), executive names, or "
    "firm history. If a number isn't in the facts, do not write a number. If a driver isn't "
    "in the facts, do not name one."
)


BC_OPPORTUNITY_PROMPT = """You are writing the **Opportunity** section of a 4-6 page \
RevWin Business Case for **{firm_short}** in the **{sector_display}** sector.

{voice_guardrails}

Format requirements:
  - 1 to 2 paragraphs, 100-200 words total
  - Markdown, no headings (the section heading is added separately)
  - Lead with the addressable market growth — this sector grows from the {end_year} \
size (`market_projection.end_year_m`) to the {target_year} size (`market_projection.target_year_m`).
  - Anchor on {firm_short}'s current revenue (`current_combined_revenue_m`) and current share \
(derive from primary_sector.end_share_pct, plus secondary_sector.end_share_pct if bundled).
  - If `active_growth.has_explicit_target` is true, state the target_revenue_m as the \
{target_year} goal and the net_new_required_m as what must be added to get there.
  - If `active_growth.has_explicit_target` is false, frame the opportunity as participating \
in a funded market without gaining ground in real terms — quantify the real-CAGR gap from \
`primary_sector.delta_pp` (and secondary_sector.delta_pp when bundled).
  - Conclude with: this is achievable through consistent capture-planning execution at \
pursuit volume, not occasional must-win heroics.

Facts:

```json
{facts_json}
```

Now write the prose only — no preamble, no postscript, no markdown fences.
"""


BC_WHY_SECTOR_PROMPT = """You are writing the **Why {sector_display} First** section of a \
RevWin Business Case for **{firm_short}**.

{voice_guardrails}

Format requirements:
  - 3 to 5 short paragraphs, each beginning with a brief **bold lead phrase.**
  - 200-350 words total
  - Markdown, no headings (the section heading is added separately)

Themes to draw from (use only when the data in the facts supports them — skip themes \
that aren't supported):

  1. **High pursuit volume with repeatable buyer architecture.** This is universal for \
any sector picked — pursuit-planning lift compounds where the firm runs a steady volume \
of similar pursuits with similar buyers.

  2. **Stabilized but not-yet-gaining share — clean measurement baseline.** Use this when \
`primary_sector.delta_pp` is small in magnitude (within ~1.0pp of zero). The firm is \
neither gaining nor losing ground in real terms, which makes any pilot lift cleanly \
attributable to the methodology rather than a sector tailwind.

  3. **Imminent acquisition or expansion cycle.** Use ONLY if the research notes (visible \
in the facts via the parent prompt — but you do not have that here) clearly establish a \
named acquisition or expansion cycle. If the facts do not mention one, do not invent one.

  4. **Cultural fit / firm history with the methodology.** Use ONLY if the facts establish \
prior history with the methodology. Do not invent prior engagements.

Default to themes 1 and 2 unless the facts clearly support 3 or 4.

Facts:

```json
{facts_json}
```

Now write the prose only — no preamble, no postscript, no markdown fences.
"""


BC_THE_ASK_PROMPT = """You are writing the closing **The Ask** section of a RevWin Business \
Case for **{firm_short}** in the **{sector_display}** sector.

{voice_guardrails}

Format requirements:
  - 1 paragraph, 80-150 words
  - Markdown, no headings (the section heading is added separately)
  - Propose a specific 60-minute working session.
  - If `growth_org_leaders` is non-empty, name 1-2 of those leaders as suggested attendees. \
Otherwise reference "{firm_short} {sector_display} Business Group leadership" generically.
  - Specify what AEC Market Masters provides (the platform, an onboarding intensive in \
weeks 0-2, and embedded coaching through the {pilot_duration_quarters}-quarter pilot) \
and what the firm provides (pursuit-team time and access to historical pursuit data).
  - Close on the bottom-line framing: a {pilot_duration_quarters}-quarter Mid-scenario \
pilot returns roughly the Mid scenario's incremental_fee_revenue_m on the Mid scenario's \
pilot_cost_m — quote those numbers from `roi_scenarios[1]`.

Facts:

```json
{facts_json}
```

Now write the prose only — no preamble, no postscript, no markdown fences.
"""


def _bc_render(prompt: str, *, api_key: str | None, model: str, max_tokens: int) -> str:
    return _call_llm(prompt, api_key, model, max_tokens)


def render_bc_opportunity(
    bc_inputs,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 800,
) -> str:
    """Generate the Opportunity narrative for a Business Case."""
    facts = bc_inputs.to_facts_dict()
    prompt = BC_OPPORTUNITY_PROMPT.format(
        firm_short=bc_inputs.firm_short,
        sector_display=bc_inputs.sector_pick.display_label,
        end_year=bc_inputs.end_year,
        target_year=bc_inputs.target_year,
        voice_guardrails=_BC_VOICE_GUARDRAILS,
        facts_json=json.dumps(facts, indent=2, default=str),
    )
    return _bc_render(prompt, api_key=api_key, model=model, max_tokens=max_tokens)


def render_bc_why_sector(
    bc_inputs,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1000,
) -> str:
    """Generate the Why-Sector narrative for a Business Case."""
    facts = bc_inputs.to_facts_dict()
    prompt = BC_WHY_SECTOR_PROMPT.format(
        firm_short=bc_inputs.firm_short,
        sector_display=bc_inputs.sector_pick.display_label,
        voice_guardrails=_BC_VOICE_GUARDRAILS,
        facts_json=json.dumps(facts, indent=2, default=str),
    )
    return _bc_render(prompt, api_key=api_key, model=model, max_tokens=max_tokens)


def render_bc_the_ask(
    bc_inputs,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 600,
) -> str:
    """Generate the closing Ask paragraph for a Business Case."""
    facts = bc_inputs.to_facts_dict()
    prompt = BC_THE_ASK_PROMPT.format(
        firm_short=bc_inputs.firm_short,
        sector_display=bc_inputs.sector_pick.display_label,
        pilot_duration_quarters=bc_inputs.pilot_duration_quarters,
        voice_guardrails=_BC_VOICE_GUARDRAILS,
        facts_json=json.dumps(facts, indent=2, default=str),
    )
    return _bc_render(prompt, api_key=api_key, model=model, max_tokens=max_tokens)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(HERE / "lib"))
    from ingest import build_panel, load_cci_annual
    from resolve import get_firm_panel, resolve as resolve_fn
    from charts import build_composite_by_year
    from compute import compute_section_facts

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

    text = render_sector_narrative(facts)
    print(text)
