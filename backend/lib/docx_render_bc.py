"""
docx_render_bc.py — assemble the RevWin Business Case Word document.

A 4-6 page sales document distinct from the 50-page Market Analysis. Layout:

  - Header strip: "BUSINESS CASE" / firm bold title / sector subtitle
  - 5-column header summary table (revenue / target / net new / scope / duration)
  - "The Ask" callout (light-tan fill, accent border)
  - 1. The Opportunity (LLM-generated)
  - 2. The Problem (universal templated)
  - "What scaling means in practice" callout (templated)
  - 3. The Solution: RevWin AI-Native Capture Planning (templated)
  - 4. Why [Sector] First (LLM-generated)
  - 5. Pilot Scope (2-column table) + Success Metrics block
  - 6. ROI Logic (preamble + Conservative/Mid/Aggressive table)
  - 7. Timeline (2-column table)
  - 8. The Ask (LLM-generated closing)
  - "Bottom Line" callout

The Problem / Solution / Pilot Scope / Success Metrics / Timeline templated
content is universal across firms and pattern-matched from the canonical HDR
Water reference business case.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from business_case import BusinessCaseInputs


# ---------------------------------------------------------------------------
#  Number formatters (matched to docx_render.py conventions)
# ---------------------------------------------------------------------------


def _fmt_money_m_or_b(v_m: Optional[float]) -> str:
    """$48M / $791M / $1.28B / $22.4B."""
    if v_m is None:
        return "—"
    if abs(v_m) >= 1000:
        return f"${v_m / 1000:.2f}B"
    return f"${v_m:,.0f}M"


def _fmt_money_signed(v_m: Optional[float]) -> str:
    """+$371M / -$120M."""
    if v_m is None:
        return "—"
    sign = "+" if v_m >= 0 else "-"
    return f"{sign}{_fmt_money_m_or_b(abs(v_m))}"


def _fmt_pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"


def _fmt_int_x(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}×" if v >= 10 else f"{v:.1f}×"


# ---------------------------------------------------------------------------
#  Templated content (universal across firms — pattern of HDR Water reference)
# ---------------------------------------------------------------------------


_PROBLEM_PARAGRAPHS = [
    "Most AEC firms run capture planning on heroics. Senior leaders carry the institutional memory; pursuit teams reinvent the wheel on every must-win. The result is a small number of brilliantly-captured opportunities surrounded by a long tail of pursuits that get the same generic treatment regardless of stakes — and a 30% win rate that nobody is sure how to move.",
    "The constraint isn't talent. It's that capture-planning rigor doesn't scale across the volume of pursuits a top-tier firm runs in a single quarter. Account leaders can carry maybe a dozen opportunities through a deep capture process per cycle. Everything else gets a Tuesday-afternoon SWOT and a guess at the win theme. The pursuits the firm could win — the ones where a real plan would change the outcome — are statistically distributed across the full pipeline, not concentrated in the dozen the leader can personally workshop.",
    "Lifting the median pursuit closer to the rigor of the top decile is where the next 8-15 percentage points of win-rate live. The question is whether that lift can be delivered at pursuit volume — without expanding the senior bench, without slowing the schedule, and without forcing pursuit teams to learn a new methodology from a textbook.",
]


_SCALING_CALLOUT = (
    "Scaling capture planning means giving every pursuit team — not just the must-win pursuits — "
    "the same rigor: a coached SWOT, a tested win theme, named decision-makers, a competitive "
    "read, and a debrief loop. The tools to do that exist; the missing piece is operating them at "
    "the firm's actual pursuit volume."
)


_SOLUTION_PARAGRAPHS = [
    "**RevWin is the AI-native capture planning platform built for AEC firms operating at top-25 ENR scale.** It delivers capture-planning rigor at pursuit volume, with embedded coaching from the same methodology AEC Market Masters has used to lift firm-level win rates for 25+ years.",
    "The platform combines three layers: (1) a structured capture workflow that asks every pursuit team the right questions in the right order — SWOT, win theme, decision-makers, competitive landscape, debrief; (2) AI-assisted drafts that compress the time-to-first-coherent-plan from days to hours, anchored on the firm's own historical pursuit data; (3) embedded coaching by AEC Market Masters consultants who review the highest-stakes pursuits and run quarterly synthesis sessions across the pursuit team.",
    "The deliverable is a measurable, repeatable lift in win rate across the pursuit pipeline — not just on the dozen pursuits the senior bench was already going to win.",
]


_PILOT_SCOPE_ROWS = [
    ("Sector", "{sector_label}"),
    ("Pursuit volume", "{pilot_volume_mid} pursuits over {duration_months} months"),
    ("Average pursuit fee size", "{avg_pursuit_fee_m} per win"),
    ("Pilot duration", "{duration_quarters} quarters ({duration_months} months)"),
    ("Investment", "{pilot_cost_m_mid} (Mid scenario)"),
    ("Coverage", "All {sector_label} pursuits over the pilot window — not a hand-selected subset"),
    ("Coaching cadence", "Weekly working sessions with pursuit captains; monthly leadership review"),
]


_SUCCESS_METRICS = [
    "**Win-rate uplift.** Measured on a same-pursuit-mix basis vs. the firm's prior 8 quarters. Target: +8-15 percentage points (Conservative/Aggressive band).",
    "**Coverage.** % of in-scope pursuits with a completed capture plan ≥ 7 days before submission. Target: 90%+.",
    "**Time-to-plan.** Median elapsed time from go/no-go decision to a coached, signed-off capture plan. Target: < 14 days.",
    "**Plan rigor.** % of plans with named decision-makers, a written win theme, and a documented competitive read. Target: 95%+.",
    "**Coaching engagement.** Pursuit-captain attendance at weekly working sessions. Target: 90%+.",
]


_TIMELINE_ROWS = [
    ("Weeks 0-2",   "Onboarding intensive: methodology training, platform configuration, historical-pursuit data import, baseline win-rate measurement."),
    ("Weeks 2-8",   "Embed: every in-scope pursuit runs through the coached capture workflow. Weekly pursuit-captain sessions; daily Slack-channel coaching."),
    ("Weeks 8-26",  "Steady-state pilot: pursuit teams operate with platform support, AEC Market Masters coaches the highest-stakes pursuits, monthly leadership reviews track win-rate evolution."),
    ("Week 26",     "Pilot retrospective: same-mix win-rate measurement vs. baseline, qualitative debrief, decision on full-firm rollout."),
]


def _format_pilot_scope_rows(bc: BusinessCaseInputs) -> list[tuple[str, str]]:
    """Substitute pilot-scope template tokens against the Mid scenario."""
    mid = bc.roi_table.scenarios[1]   # Conservative / Mid / Aggressive
    duration_q = bc.pilot_duration_quarters
    duration_m = duration_q * 3
    repl = {
        "sector_label":       bc.sector_pick.display_label,
        "pilot_volume_mid":   str(mid.pilot_volume),
        "avg_pursuit_fee_m":  _fmt_money_m_or_b(mid.avg_pursuit_fee_m),
        "duration_quarters":  str(duration_q),
        "duration_months":    str(duration_m),
        "pilot_cost_m_mid":   _fmt_money_m_or_b(mid.pilot_cost_m),
    }
    out = []
    for label, template in _PILOT_SCOPE_ROWS:
        try:
            out.append((label, template.format(**repl)))
        except KeyError:
            out.append((label, template))
    return out


# ---------------------------------------------------------------------------
#  Spec builder
# ---------------------------------------------------------------------------


def build_business_case_spec(
    bc: BusinessCaseInputs,
    primary_color_hex: str,
    opportunity_md: str,
    why_sector_md: str,
    the_ask_md: str,
    publish_date: str,
) -> dict:
    """Convert all the structured inputs into the spec dict the JS builder consumes."""
    sp = bc.sector_pick
    mp = bc.market_projection
    ag = bc.active_growth

    # 5-column header summary table
    target_col_label = "Active Growth Target" if ag.has_explicit_target else "Baseline Maintain-Share"
    if ag.has_explicit_target:
        target_col_value = (
            f"{_fmt_money_m_or_b(ag.target_revenue_m)} "
            f"({_fmt_pct((ag.target_share or 0) * 100)} of {bc.target_year} market)"
        )
        net_new_value = _fmt_money_signed(ag.net_new_required_m)
        net_new_positive = (ag.net_new_required_m or 0) > 0
    else:
        # Baseline: maintain current share against the projected market
        current_share = 0.0
        if mp.end_year_m and ag.current_revenue_m:
            current_share = ag.current_revenue_m / mp.end_year_m
        baseline_target = current_share * mp.target_year_m
        net_new = baseline_target - (ag.current_revenue_m or 0)
        target_col_value = (
            f"{_fmt_money_m_or_b(baseline_target)} "
            f"(hold {_fmt_pct(current_share * 100, 2)} share)"
        )
        net_new_value = _fmt_money_signed(net_new)
        net_new_positive = net_new > 0

    duration_q = bc.pilot_duration_quarters
    mid = bc.roi_table.scenarios[1]
    pilot_scope_value = (
        f"{mid.pilot_volume} pursuits"
    )

    header_table = {
        "columns": [
            {
                "label": f"{bc.end_year} {sp.display_label} Revenue",
                "value": _fmt_money_m_or_b(ag.current_revenue_m),
                "highlight": False,
            },
            {
                "label": f"{bc.target_year} {target_col_label}",
                "value": target_col_value,
                "highlight": False,
            },
            {
                "label": "Net New",
                "value": net_new_value,
                "highlight": net_new_positive,   # green when positive
            },
            {
                "label": "Pilot Scope",
                "value": pilot_scope_value,
                "highlight": False,
            },
            {
                "label": "Pilot Duration",
                "value": f"{duration_q} quarters",
                "highlight": False,
            },
        ],
    }

    pilot_scope_rows = [
        {"label": label, "value": value}
        for label, value in _format_pilot_scope_rows(bc)
    ]

    roi_rows = [
        {
            "scenario": s.label,
            "pilotVolume": s.pilot_volume,
            "winRateUpliftPp": _fmt_pct(s.win_rate_uplift_pp, 0),
            "avgPursuitFee": _fmt_money_m_or_b(s.avg_pursuit_fee_m),
            "incrementalWins": f"{s.incremental_wins:.1f}",
            "incrementalRevenue": _fmt_money_m_or_b(s.incremental_fee_revenue_m),
            "pilotCost": _fmt_money_m_or_b(s.pilot_cost_m),
            "firstCycleRoi": _fmt_int_x(s.first_cycle_roi),
        }
        for s in bc.roi_table.scenarios
    ]

    timeline_rows = [{"phase": phase, "detail": detail} for phase, detail in _TIMELINE_ROWS]

    leaders = []
    if bc.research and getattr(bc.research, "strategicInitiative", None):
        leaders = list(bc.research.strategicInitiative.growthOrgLeaders or [])
    if not leaders:
        leaders = [f"{bc.firm_short} {sp.display_label} Business Group leadership"]

    bottom_line = (
        f"A {duration_q}-quarter pilot at the Mid scenario produces "
        f"{_fmt_money_m_or_b(mid.incremental_fee_revenue_m)} in incremental fee revenue "
        f"on a {_fmt_money_m_or_b(mid.pilot_cost_m)} investment — a "
        f"{_fmt_int_x(mid.first_cycle_roi)} first-cycle ROI. "
        + ("This is the path to the Active Growth target in a measurable, repeatable way."
           if ag.has_explicit_target
           else "This is the path to gaining ground in a sector where the firm currently does not.")
    )

    return {
        "meta": {
            "firmShort":        bc.firm_short,
            "firmLegalName":    bc.firm_legal_name,
            "sectorDisplayLabel": sp.display_label,
            "endYear":          bc.end_year,
            "targetYear":       bc.target_year,
            "primaryColorHex":  primary_color_hex,
            "publishDate":      publish_date,
            "hasExplicitTarget": ag.has_explicit_target,
        },
        "headerTable": header_table,
        "askCallout": {
            "title": "The Ask",
            "body": (
                f"A 60-minute working session with {bc.firm_short} "
                f"{sp.display_label} Business Group leadership to walk through this "
                f"business case in detail and define the {duration_q}-quarter pilot."
            ),
            "leaders": leaders,
        },
        "opportunity": {
            "heading": "1. The Opportunity",
            "bodyMd":  opportunity_md,
        },
        "problem": {
            "heading": "2. The Problem",
            "paragraphs": _PROBLEM_PARAGRAPHS,
        },
        "scalingCallout": {
            "title": "What scaling means in practice",
            "body":  _SCALING_CALLOUT,
        },
        "solution": {
            "heading": "3. The Solution: RevWin AI-Native Capture Planning",
            "paragraphsMd": _SOLUTION_PARAGRAPHS,
        },
        "whySector": {
            "heading": f"4. Why {sp.display_label} First",
            "bodyMd":  why_sector_md,
        },
        "pilotScope": {
            "heading":  f"5. Pilot Scope: {sp.display_label}",
            "rows":     pilot_scope_rows,
            "successHeading": "Success Metrics",
            "successMd": _SUCCESS_METRICS,
        },
        "roi": {
            "heading": "6. ROI Logic",
            "preambleMd": (
                "Three scenarios bound the expected return. The Mid scenario assumes the "
                f"win-rate uplift and pursuit volume {bc.firm_short} should expect to see in "
                "a sector with the platform's full coaching cadence. The Conservative scenario "
                "assumes lower uplift and lower pursuit volume — closer to a sector where the "
                "firm is starting from a smaller pursuit base. The Aggressive scenario assumes "
                "the upper-end uplift the methodology has produced in prior engagements."
            ),
            "rows":      roi_rows,
        },
        "timeline": {
            "heading": "7. Timeline",
            "rows":    timeline_rows,
        },
        "ask": {
            "heading": "8. The Ask",
            "bodyMd":  the_ask_md,
        },
        "bottomLine": {
            "title": "Bottom Line",
            "body":  bottom_line,
        },
    }


# ---------------------------------------------------------------------------
#  JS builder
# ---------------------------------------------------------------------------


DOCX_BC_BUILDER_JS = r"""
// docx_bc_builder.js — assemble the RevWin Business Case .docx.
// Reads spec from JSON on stdin; writes .docx to argv[2].

const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun,
  Table, TableRow, TableCell, BorderStyle, WidthType, ShadingType,
  AlignmentType, Header, Footer, PageBreak, PageNumber,
} = require('docx');

const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const outPath = process.argv[2];

const PAGE_WIDTH = 12240;
const PAGE_HEIGHT = 15840;
const MARGIN = 1080;
const CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN;

const ACCENT = (spec.meta && spec.meta.primaryColorHex) ? spec.meta.primaryColorHex : '2C3E50';
const ACCENT_FILL = ACCENT;
const CALLOUT_FILL = 'F8F4EE';
const CALLOUT_BORDER = ACCENT;
const NET_NEW_GREEN = '0E7C3A';
const TABLE_HEADER_BG = '2C3E50';
const TABLE_HEADER_TEXT = 'FFFFFF';
const ROW_ALT_BG = 'FAFAFA';

function P(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, ...(opts.run || {}) })],
    ...(opts.paragraph || {}),
  });
}

function H1(text) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 32, font: 'Calibri', color: '1A1A1A' })],
    spacing: { before: 320, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 4 } },
    keepNext: true, keepLines: true,
  });
}

function H2(text) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 24, font: 'Calibri', color: ACCENT })],
    spacing: { before: 200, after: 80 },
    keepNext: true, keepLines: true,
  });
}

function bodyP(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, size: 20, font: 'Calibri', ...(opts.run || {}) })],
    spacing: { after: 120, line: 280 },
    alignment: opts.alignment || AlignmentType.JUSTIFIED,
  });
}

// **bold** spans only — same simple parser used by the Market Analysis builder
function mdToParagraphs(md) {
  if (!md) return [];
  const blocks = Array.isArray(md) ? md : md.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
  return blocks.map(block => {
    const text = (typeof block === 'string') ? block : String(block);
    const runs = [];
    const tokenRegex = /\*\*([^*]+)\*\*/g;
    let lastEnd = 0; let match;
    while ((match = tokenRegex.exec(text)) !== null) {
      if (match.index > lastEnd) {
        runs.push(new TextRun({ text: text.slice(lastEnd, match.index), size: 20, font: 'Calibri' }));
      }
      runs.push(new TextRun({ text: match[1], size: 20, font: 'Calibri', bold: true }));
      lastEnd = match.index + match[0].length;
    }
    if (lastEnd < text.length) {
      runs.push(new TextRun({ text: text.slice(lastEnd), size: 20, font: 'Calibri' }));
    }
    if (runs.length === 0) {
      runs.push(new TextRun({ text, size: 20, font: 'Calibri' }));
    }
    return new Paragraph({
      children: runs,
      spacing: { after: 160, line: 280 },
      alignment: AlignmentType.JUSTIFIED,
    });
  });
}

// Bulleted list of markdown items. Each item may contain **bold** spans.
function mdBullets(items) {
  return items.map(item => {
    const runs = [];
    const text = String(item);
    const tokenRegex = /\*\*([^*]+)\*\*/g;
    let lastEnd = 0; let match;
    runs.push(new TextRun({ text: '•  ', size: 20, font: 'Calibri', bold: true }));
    while ((match = tokenRegex.exec(text)) !== null) {
      if (match.index > lastEnd) {
        runs.push(new TextRun({ text: text.slice(lastEnd, match.index), size: 20, font: 'Calibri' }));
      }
      runs.push(new TextRun({ text: match[1], size: 20, font: 'Calibri', bold: true }));
      lastEnd = match.index + match[0].length;
    }
    if (lastEnd < text.length) {
      runs.push(new TextRun({ text: text.slice(lastEnd), size: 20, font: 'Calibri' }));
    }
    return new Paragraph({
      children: runs,
      spacing: { after: 80, line: 280 },
      indent: { left: 360 },
    });
  });
}

function plainCell(text, opts = {}) {
  const {
    bold = false, italics = false, color = '333333', bg = null,
    align = AlignmentType.LEFT, width = null, fontSize = 20,
    borderColor = 'DDDDDD', borderStyle = BorderStyle.SINGLE, borderSize = 2,
  } = opts;
  const co = {
    children: [new Paragraph({
      children: [new TextRun({ text: String(text), bold, italics, color, font: 'Calibri', size: fontSize })],
      alignment: align,
    })],
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    borders: {
      top:    { style: borderStyle, size: borderSize, color: borderColor },
      bottom: { style: borderStyle, size: borderSize, color: borderColor },
      left:   { style: borderStyle, size: borderSize, color: borderColor },
      right:  { style: borderStyle, size: borderSize, color: borderColor },
    },
  };
  if (bg) co.shading = { fill: bg, type: ShadingType.CLEAR };
  if (width) co.width = { size: width, type: WidthType.DXA };
  return new TableCell(co);
}

// ---------- Header strip ----------

const allChildren = [];

allChildren.push(new Paragraph({
  children: [new TextRun({ text: 'BUSINESS CASE', bold: true, size: 22, font: 'Calibri', color: ACCENT, characterSpacing: 60 })],
  alignment: AlignmentType.LEFT,
  spacing: { before: 60, after: 60 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: spec.meta.firmShort, bold: true, size: 56, font: 'Calibri', color: ACCENT })],
  alignment: AlignmentType.LEFT,
  spacing: { after: 60 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({
    text: 'RevWin Capture Planning Pilot — ' + spec.meta.sectorDisplayLabel,
    size: 24, font: 'Calibri', color: '555555', italics: true,
  })],
  alignment: AlignmentType.LEFT,
  spacing: { after: 240 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: ACCENT, space: 8 } },
}));

// ---------- 5-column header summary table ----------

(function () {
  const cols = spec.headerTable.columns;
  const w = Math.floor(CONTENT_WIDTH / cols.length);

  const headerRow = new TableRow({
    children: cols.map(c => plainCell(c.label, {
      bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG,
      align: AlignmentType.CENTER, width: w, fontSize: 16,
    })),
  });
  const valueRow = new TableRow({
    children: cols.map(c => plainCell(c.value, {
      bold: true,
      color: c.highlight ? NET_NEW_GREEN : ACCENT,
      align: AlignmentType.CENTER, width: w, fontSize: 22, bg: 'FAFAFA',
    })),
  });

  allChildren.push(new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: cols.map(() => w),
    rows: [headerRow, valueRow],
  }));
  allChildren.push(P(' ', { run: { size: 14 } }));
})();

// ---------- The Ask callout ----------

(function () {
  const ask = spec.askCallout;
  const cellChildren = [];
  cellChildren.push(new Paragraph({
    children: [new TextRun({ text: ask.title, bold: true, size: 22, font: 'Calibri', color: ACCENT })],
    spacing: { after: 80 },
  }));
  cellChildren.push(new Paragraph({
    children: [new TextRun({ text: ask.body, size: 20, font: 'Calibri', color: '333333' })],
    spacing: { after: 80, line: 280 },
  }));
  if (ask.leaders && ask.leaders.length) {
    cellChildren.push(new Paragraph({
      children: [new TextRun({ text: 'Suggested attendees: ', bold: true, size: 18, font: 'Calibri', color: '555555' }),
                 new TextRun({ text: ask.leaders.join('; '), size: 18, font: 'Calibri', color: '555555' })],
      spacing: { after: 40 },
    }));
  }

  const callout = new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [CONTENT_WIDTH],
    rows: [new TableRow({
      children: [new TableCell({
        children: cellChildren,
        margins: { top: 200, bottom: 200, left: 240, right: 240 },
        shading: { fill: CALLOUT_FILL, type: ShadingType.CLEAR },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          bottom: { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          left:   { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          right:  { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
        },
      })],
    })],
  });
  allChildren.push(callout);
  allChildren.push(P(' ', { run: { size: 14 } }));
})();

// ---------- 1. The Opportunity ----------

allChildren.push(H1(spec.opportunity.heading));
allChildren.push(...mdToParagraphs(spec.opportunity.bodyMd));

// ---------- 2. The Problem ----------

allChildren.push(H1(spec.problem.heading));
for (const para of spec.problem.paragraphs) {
  allChildren.push(...mdToParagraphs(para));
}

// "What scaling means in practice" callout
(function () {
  const sc = spec.scalingCallout;
  const callout = new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [CONTENT_WIDTH],
    rows: [new TableRow({
      children: [new TableCell({
        children: [
          new Paragraph({
            children: [new TextRun({ text: sc.title, bold: true, italics: true, size: 20, font: 'Calibri', color: ACCENT })],
            spacing: { after: 60 },
          }),
          new Paragraph({
            children: [new TextRun({ text: sc.body, size: 19, font: 'Calibri', color: '333333', italics: true })],
            spacing: { line: 280 },
          }),
        ],
        margins: { top: 160, bottom: 160, left: 200, right: 200 },
        shading: { fill: CALLOUT_FILL, type: ShadingType.CLEAR },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 12, color: CALLOUT_BORDER },
          bottom: { style: BorderStyle.SINGLE, size: 12, color: CALLOUT_BORDER },
          left:   { style: BorderStyle.SINGLE, size: 36, color: CALLOUT_BORDER },
          right:  { style: BorderStyle.SINGLE, size: 12, color: CALLOUT_BORDER },
        },
      })],
    })],
  });
  allChildren.push(callout);
  allChildren.push(P(' ', { run: { size: 14 } }));
})();

// ---------- 3. The Solution ----------

allChildren.push(H1(spec.solution.heading));
for (const para of spec.solution.paragraphsMd) {
  allChildren.push(...mdToParagraphs(para));
}

// ---------- 4. Why [Sector] First ----------

allChildren.push(H1(spec.whySector.heading));
allChildren.push(...mdToParagraphs(spec.whySector.bodyMd));

// ---------- 5. Pilot Scope ----------

allChildren.push(H1(spec.pilotScope.heading));
(function () {
  const w_label = Math.floor(CONTENT_WIDTH * 0.30);
  const w_value = CONTENT_WIDTH - w_label;
  const rows = spec.pilotScope.rows.map((r, i) => new TableRow({
    children: [
      plainCell(r.label, { bold: true, color: '1A1A1A', width: w_label, bg: i % 2 === 1 ? ROW_ALT_BG : null, align: AlignmentType.LEFT }),
      plainCell(r.value, { color: '333333', width: w_value, bg: i % 2 === 1 ? ROW_ALT_BG : null, align: AlignmentType.LEFT }),
    ],
  }));
  allChildren.push(new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [w_label, w_value],
    rows,
  }));
})();
allChildren.push(P(' ', { run: { size: 14 } }));
allChildren.push(H2(spec.pilotScope.successHeading));
allChildren.push(...mdBullets(spec.pilotScope.successMd));

// ---------- 6. ROI Logic ----------

allChildren.push(H1(spec.roi.heading));
allChildren.push(...mdToParagraphs(spec.roi.preambleMd));

(function () {
  const headers = ['Scenario', 'Volume', 'Win-rate Uplift', 'Avg Pursuit Fee', 'Wins', 'Inc. Fee Revenue', 'Pilot Cost', '1st-cycle ROI'];
  const widths = [1500, 800, 1300, 1300, 800, 1500, 1100, 1060];

  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => plainCell(h, {
      bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG,
      align: AlignmentType.CENTER, width: widths[i], fontSize: 16,
    })),
  });
  const dataRows = spec.roi.rows.map((r, idx) => {
    const isMid = idx === 1;
    const bg = isMid ? 'FFF8E5' : (idx % 2 === 1 ? ROW_ALT_BG : null);
    return new TableRow({
      children: [
        plainCell(r.scenario,           { bold: true, color: '1A1A1A', bg, width: widths[0], align: AlignmentType.LEFT }),
        plainCell(r.pilotVolume,        { color: '333333', bg, width: widths[1], align: AlignmentType.CENTER }),
        plainCell(r.winRateUpliftPp,    { color: '333333', bg, width: widths[2], align: AlignmentType.CENTER }),
        plainCell(r.avgPursuitFee,      { color: '333333', bg, width: widths[3], align: AlignmentType.CENTER }),
        plainCell(r.incrementalWins,    { color: '333333', bg, width: widths[4], align: AlignmentType.CENTER }),
        plainCell(r.incrementalRevenue, { bold: true, color: NET_NEW_GREEN, bg, width: widths[5], align: AlignmentType.CENTER }),
        plainCell(r.pilotCost,          { color: '333333', bg, width: widths[6], align: AlignmentType.CENTER }),
        plainCell(r.firstCycleRoi,      { bold: true, color: ACCENT, bg, width: widths[7], align: AlignmentType.CENTER }),
      ],
    });
  });
  allChildren.push(new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: widths,
    rows: [headerRow, ...dataRows],
  }));
})();

// ---------- 7. Timeline ----------

allChildren.push(H1(spec.timeline.heading));
(function () {
  const w_phase = Math.floor(CONTENT_WIDTH * 0.22);
  const w_detail = CONTENT_WIDTH - w_phase;
  const rows = spec.timeline.rows.map((r, i) => new TableRow({
    children: [
      plainCell(r.phase,  { bold: true, color: ACCENT, width: w_phase, bg: i % 2 === 1 ? ROW_ALT_BG : null, align: AlignmentType.LEFT }),
      plainCell(r.detail, { color: '333333', width: w_detail, bg: i % 2 === 1 ? ROW_ALT_BG : null, align: AlignmentType.LEFT }),
    ],
  }));
  allChildren.push(new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [w_phase, w_detail],
    rows,
  }));
})();
allChildren.push(P(' ', { run: { size: 14 } }));

// ---------- 8. The Ask ----------

allChildren.push(H1(spec.ask.heading));
allChildren.push(...mdToParagraphs(spec.ask.bodyMd));

// ---------- Bottom Line callout ----------

(function () {
  const bl = spec.bottomLine;
  const callout = new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: [CONTENT_WIDTH],
    rows: [new TableRow({
      children: [new TableCell({
        children: [
          new Paragraph({
            children: [new TextRun({ text: bl.title, bold: true, size: 24, font: 'Calibri', color: ACCENT })],
            spacing: { after: 80 },
          }),
          new Paragraph({
            children: [new TextRun({ text: bl.body, size: 22, font: 'Calibri', color: '1A1A1A' })],
            spacing: { line: 320 },
          }),
        ],
        margins: { top: 200, bottom: 200, left: 240, right: 240 },
        shading: { fill: CALLOUT_FILL, type: ShadingType.CLEAR },
        borders: {
          top:    { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          bottom: { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          left:   { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
          right:  { style: BorderStyle.SINGLE, size: 24, color: CALLOUT_BORDER },
        },
      })],
    })],
  });
  allChildren.push(callout);
})();

// ---------- Build doc ----------

const doc = new Document({
  styles: { default: { document: { run: { font: 'Calibri', size: 20 } } } },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_WIDTH, height: PAGE_HEIGHT },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          children: [new TextRun({
            text: `${spec.meta.firmShort} · RevWin Business Case · ${spec.meta.sectorDisplayLabel} · ${spec.meta.publishDate}`,
            italics: true, size: 16, color: '888888', font: 'Calibri',
          })],
          alignment: AlignmentType.RIGHT,
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          children: [
            new TextRun({ text: 'Page ', size: 16, color: '888888', font: 'Calibri' }),
            new TextRun({ children: [PageNumber.CURRENT], size: 16, color: '888888', font: 'Calibri' }),
          ],
          alignment: AlignmentType.RIGHT,
        })],
      }),
    },
    children: allChildren,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log('Wrote: ' + outPath);
}).catch(err => {
  console.error(err);
  process.exit(1);
});
"""


def render_business_case_docx(spec: dict, output_path: Path) -> Path:
    """Run the JS docx_bc_builder with the given spec dict, write to output_path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    builder_js = output_path.parent / "_docx_bc_builder.js"
    builder_js.write_text(DOCX_BC_BUILDER_JS, encoding="utf-8")

    spec_json = json.dumps(spec, default=str)

    proc = subprocess.run(
        ["node", str(builder_js), str(output_path)],
        input=spec_json, text=True,
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"business case docx build failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )
    return output_path
