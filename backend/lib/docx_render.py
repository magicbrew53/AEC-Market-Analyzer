"""
docx_render.py — assemble the firm market-analysis Word document.

Layout matches HDR_Market_Analysis_vs_Composite_2005-2026.docx:

  - Title page (firm name, subtitle, sources, date)
  - Executive Summary (Key Findings bullets + comparison table)
  - Firm Profile (At a Glance bullets, ownership, acquisitions)
  - Methodology
  - Numbered sections, one per sector (12 total):
      1. Total Firm Revenue
      2. International Revenue
      3. General Building
      4. Manufacturing
      5. Power
      6. Water Supply
      7. Sewer/Waste Water
      8. Industrial/Petroleum
      9. Transportation
      10. Hazardous Waste
      11. Telecommunications
      12. Other
    Each section: heading, narrative prose, 5 charts, 1 comparison table
  - Strategic Growth Framework (FMI-driven; firm-specific scenarios)
  - Conclusions

Generates the file via JavaScript with the docx library, per the docx skill.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from compute import SectionFacts, YearRow


# Sector display names + canonical keys, in the order they appear in the report
SECTION_ORDER = [
    ("total",          "Total Firm Revenue"),
    ("intl",           "International Revenue"),
    ("gen_bldg",       "General Building"),
    ("manufacturing",  "Manufacturing"),
    ("power",          "Power"),
    ("water_supply",   "Water Supply"),
    ("sewer_waste",    "Sewer/Waste Water"),
    ("ind_pet",        "Industrial/Petroleum"),
    ("transportation", "Transportation"),
    ("haz_waste",      "Hazardous Waste"),
    ("telecom",        "Telecommunications"),
    ("other",          "Other"),
]


# Sector primary colors — match the chart palette for the firm column in tables.
SECTOR_PRIMARY_HEX = {
    "total":          "D62828",
    "intl":           "1F6FB4",
    "gen_bldg":       "5D6D7E",
    "manufacturing":  "7F4F8F",
    "power":          "F39C12",
    "water_supply":   "2E86AB",
    "sewer_waste":    "117A65",
    "ind_pet":        "7B5E3F",
    "transportation": "922B3E",
    "haz_waste":      "6E2C00",
    "telecom":        "1B7A4A",
    "other":          "5D5C61",
}


def _format_money_short(v_m: float) -> str:
    """Format a $M value: $670M, $1.28B, $158.71B."""
    if v_m is None:
        return "—"
    if abs(v_m) >= 1000:
        return f"${v_m/1000:.2f}B".rstrip("0").rstrip(".") + ("B" if not f"${v_m/1000:.2f}B".endswith("B") else "")
    return f"${v_m:,.0f}M"


def _format_money_b(v_m: float) -> str:
    """Force $B format: 158.71B from 158714.2."""
    if v_m is None:
        return "—"
    return f"${v_m/1000:.2f}B"


def _format_pct(v: float, decimals: int = 2, signed: bool = False) -> str:
    if v is None:
        return "—"
    if signed:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v*100:.{decimals}f}%"
    return f"{v*100:.{decimals}f}%"


def serialize_year_row(row: YearRow, sector_key: str) -> dict:
    """Convert YearRow to the dict expected by the JS docx builder."""
    # HDR Nom: small dollar values stay as $M, larger as $B
    if row.firm_nom_m is None:
        firm_nom = "—"
    elif row.firm_nom_m >= 1000:
        firm_nom = f"${row.firm_nom_m/1000:.2f}B"
    else:
        firm_nom = f"${row.firm_nom_m:,.0f}M"

    if row.firm_real_m is None:
        firm_real = "—"
    elif row.firm_real_m >= 1000:
        firm_real = f"${row.firm_real_m/1000:.2f}B"
    else:
        firm_real = f"${row.firm_real_m:,.0f}M"

    comp_nom = _format_money_b(row.comp_nom_m) if row.comp_nom_m else "—"
    comp_real = _format_money_b(row.comp_real_m) if row.comp_real_m else "—"

    return {
        "year": row.year,
        "rank": row.rank if row.rank is not None else "—",
        "firmNom": firm_nom,
        "firmReal": firm_real,
        "compNom": comp_nom,
        "compReal": comp_real,
        "share": _format_pct(row.share, decimals=2) if row.share else "—",
        "premium": _format_pct(row.yoy_premium, decimals=1, signed=True) if row.yoy_premium is not None else "—",
        "premiumPositive": (row.yoy_premium is not None and row.yoy_premium >= 0),
        "isForecast": row.is_forecast,
    }


def serialize_section(facts: SectionFacts, narrative_md: str, chart_paths: dict, section_num: int) -> dict:
    """Convert a SectionFacts + narrative + chart paths into the dict for JS."""
    return {
        "sectionNum": section_num,
        "sectorKey": facts.sector_key,
        "sectorLabel": facts.sector_label,
        "primaryColorHex": SECTOR_PRIMARY_HEX.get(facts.sector_key, "333333"),
        "narrativeMd": narrative_md,
        "charts": {
            "nominal": chart_paths.get("nominal_revenue"),
            "real": chart_paths.get("real_revenue"),
            "yoyNominal": chart_paths.get("yoy_nominal"),
            "yoyReal": chart_paths.get("yoy_real"),
            "marketShare": chart_paths.get("market_share"),
        },
        "rows": [serialize_year_row(r, facts.sector_key) for r in facts.rows],
    }


# ---------- Document builder (JS) ----------

DOCX_BUILDER_JS = r"""
// docx_builder.js — assemble a market analysis report.
// Reads spec from JSON on stdin; writes .docx to argv[2].

const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, ImageRun,
  Table, TableRow, TableCell, BorderStyle, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, PageOrientation,
  Header, Footer, PageBreak, PageNumber,
} = require('docx');

const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const outPath = process.argv[2];

// ---------- Helpers ----------

const PAGE_WIDTH = 12240;   // 8.5"
const PAGE_HEIGHT = 15840;  // 11"
const MARGIN = 1080;        // 0.75"
const CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN;

function P(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, ...opts.run })],
    ...opts.paragraph,
  });
}

function H1(text, color) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 32, font: 'Calibri', color: color || '1A1A1A' })],
    spacing: { before: 360, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: '888888', space: 4 } },
    keepNext: true,
    keepLines: true,
  });
}

function H2(text, color) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 26, font: 'Calibri', color: color || '1F6FB4' })],
    spacing: { before: 240, after: 80 },
    keepNext: true,
    keepLines: true,
  });
}

function H3(text) {
  return new Paragraph({
    children: [new TextRun({ text, bold: true, size: 22, font: 'Calibri', color: '1F6FB4' })],
    spacing: { before: 200, after: 60 },
    keepNext: true,
    keepLines: true,
  });
}

function bodyP(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, size: 20, font: 'Calibri', ...opts.run })],
    spacing: { after: 120, line: 280 },
    ...opts.paragraph,
  });
}

function bullet(text) {
  // Simple manual bullet using indent + tab; we'll skip true list formatting
  // to keep things simple. The "•" char is unavoidable here.
  return new Paragraph({
    children: [
      new TextRun({ text: '•  ', size: 20, font: 'Calibri', bold: true }),
      new TextRun({ text, size: 20, font: 'Calibri' }),
    ],
    spacing: { after: 80, line: 280 },
    indent: { left: 360 },
  });
}

// Convert markdown to docx paragraphs. Supports:
//   - Paragraph breaks (blank lines)
//   - **bold**
//   - simple inline (no other markdown)
function mdToParagraphs(md) {
  if (!md) return [];
  const blocks = md.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
  return blocks.map(block => {
    // Tokenize **bold** spans
    const runs = [];
    let i = 0;
    const tokenRegex = /\*\*([^*]+)\*\*/g;
    let lastEnd = 0;
    let match;
    while ((match = tokenRegex.exec(block)) !== null) {
      if (match.index > lastEnd) {
        runs.push(new TextRun({ text: block.slice(lastEnd, match.index), size: 20, font: 'Calibri' }));
      }
      runs.push(new TextRun({ text: match[1], size: 20, font: 'Calibri', bold: true }));
      lastEnd = match.index + match[0].length;
    }
    if (lastEnd < block.length) {
      runs.push(new TextRun({ text: block.slice(lastEnd), size: 20, font: 'Calibri' }));
    }
    if (runs.length === 0) {
      runs.push(new TextRun({ text: block, size: 20, font: 'Calibri' }));
    }
    return new Paragraph({
      children: runs,
      spacing: { after: 160, line: 280 },
      alignment: AlignmentType.JUSTIFIED,
    });
  });
}

function imageParagraph(imagePath, widthPx = 720, heightPx = 340) {
  if (!imagePath || !fs.existsSync(imagePath)) {
    return P('[chart missing]', { run: { italics: true, color: '999999' } });
  }
  const data = fs.readFileSync(imagePath);
  return new Paragraph({
    children: [new ImageRun({
      data,
      transformation: { width: widthPx, height: heightPx },
      type: 'png',
    })],
    spacing: { before: 80, after: 120 },
    alignment: AlignmentType.CENTER,
  });
}

// ---------- Comparison table ----------

const TABLE_HEADER_BG = '2C3E50';
const TABLE_HEADER_TEXT = 'FFFFFF';
const ROW_ALT_BG = 'FAFAFA';
const FORECAST_BG = 'FFF3DC';
const POSITIVE_GREEN = '1B7A4A';
const NEGATIVE_RED = 'B83227';
const RANK_GREY = '999999';

function tableCell(text, opts = {}) {
  const {
    bold = false, color = '333333', bg = null, align = AlignmentType.CENTER,
    width = null, fontSize = 18,
  } = opts;
  const cellOpts = {
    children: [new Paragraph({
      children: [new TextRun({ text: String(text), bold, color, font: 'Calibri', size: fontSize })],
      alignment: align,
    })],
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 2, color: 'DDDDDD' },
      bottom: { style: BorderStyle.SINGLE, size: 2, color: 'DDDDDD' },
      left: { style: BorderStyle.SINGLE, size: 2, color: 'DDDDDD' },
      right: { style: BorderStyle.SINGLE, size: 2, color: 'DDDDDD' },
    },
  };
  if (bg) cellOpts.shading = { fill: bg, type: ShadingType.CLEAR };
  if (width) cellOpts.width = { size: width, type: WidthType.DXA };
  return new TableCell(cellOpts);
}

function comparisonTable(rows, primaryColorHex) {
  // Columns: Year | Rank | Firm Nom | Firm Real | ENR Nom | ENR Real | Firm % | YoY Premium
  // Total content width = 9360 DXA. Widths sum to 9360.
  const colWidths = [780, 720, 1280, 1180, 1180, 1180, 1100, 1940];
  const sumWidths = colWidths.reduce((a, b) => a + b, 0);

  const headerCells = [
    tableCell('Year',     { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[0] }),
    tableCell('Rank',     { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[1] }),
    tableCell('Firm Nom', { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[2] }),
    tableCell('Firm Real',{ bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[3] }),
    tableCell('ENR Nom',  { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[4] }),
    tableCell('ENR Real', { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[5] }),
    tableCell('Firm %',   { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[6] }),
    tableCell('YoY Premium', { bold: true, color: TABLE_HEADER_TEXT, bg: TABLE_HEADER_BG, width: colWidths[7] }),
  ];

  const dataRows = rows.map((r, idx) => {
    const altBg = idx % 2 === 1 ? ROW_ALT_BG : null;
    const rowBg = r.isForecast ? FORECAST_BG : altBg;
    const premColor = r.premiumPositive ? POSITIVE_GREEN : NEGATIVE_RED;
    return new TableRow({
      cantSplit: true,
      children: [
        tableCell(r.year, { bold: true, bg: rowBg, width: colWidths[0] }),
        tableCell(r.rank, { color: RANK_GREY, bg: rowBg, width: colWidths[1] }),
        tableCell(r.firmNom, { bold: true, color: primaryColorHex, bg: rowBg, width: colWidths[2] }),
        tableCell(r.firmReal, { color: '555555', bg: rowBg, width: colWidths[3] }),
        tableCell(r.compNom, { color: '555555', bg: rowBg, width: colWidths[4] }),
        tableCell(r.compReal, { color: '555555', bg: rowBg, width: colWidths[5] }),
        tableCell(r.share, { bold: true, bg: rowBg, width: colWidths[6] }),
        tableCell(r.premium, { bold: true, color: premColor, bg: rowBg, width: colWidths[7] }),
      ],
    });
  });

  return new Table({
    width: { size: sumWidths, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [new TableRow({ children: headerCells, tableHeader: true }), ...dataRows],
  });
}

// ---------- Build the document ----------

const sections = spec.sections;
const meta = spec.meta;

const allChildren = [];

// --- Title page ---
allChildren.push(new Paragraph({ children: [new TextRun({ text: '', size: 24 })], spacing: { before: 1800 } }));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: 'MARKET RESEARCH REPORT', bold: true, size: 28, font: 'Calibri', color: '2C3E50' })],
  alignment: AlignmentType.CENTER,
  spacing: { after: 240 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: meta.firmShort, bold: true, size: 56, font: 'Calibri', color: meta.primaryColorHex || '1A1A1A' })],
  alignment: AlignmentType.CENTER,
  spacing: { after: 160 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: 'Composite Benchmarking', size: 32, font: 'Calibri', color: '555555' })],
  alignment: AlignmentType.CENTER,
  spacing: { after: 120 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: `Revenue & Sector Analysis, ${meta.startYear}–${meta.endYear}`, italics: true, size: 26, font: 'Calibri', color: '555555' })],
  alignment: AlignmentType.CENTER,
  spacing: { after: 120 },
}));
allChildren.push(new Paragraph({
  children: [new TextRun({ text: `${meta.firmShort} vs. ENR Top 500 Design Firms Composite`, bold: true, size: 24, font: 'Calibri', color: '1A1A1A' })],
  alignment: AlignmentType.CENTER,
  spacing: { after: 600 },
}));

allChildren.push(new Paragraph({
  children: [new TextRun({ text: 'Sources:', bold: true, size: 18, font: 'Calibri', color: '555555' })],
  alignment: AlignmentType.CENTER,
}));
for (const src of meta.sources || []) {
  allChildren.push(new Paragraph({
    children: [new TextRun({ text: src, size: 18, font: 'Calibri', color: '555555' })],
    alignment: AlignmentType.CENTER,
  }));
}
allChildren.push(new Paragraph({
  children: [new TextRun({ text: `Published: ${meta.publishDate}`, italics: true, size: 18, font: 'Calibri', color: '555555' })],
  alignment: AlignmentType.CENTER,
  spacing: { before: 200 },
}));
allChildren.push(new Paragraph({ children: [new PageBreak()] }));

// --- Executive Summary ---
if (spec.execSummary) {
  allChildren.push(H1('Executive Summary'));
  allChildren.push(H2('Key Findings', meta.primaryColorHex));
  if (spec.execSummary.keyFindingsMd) {
    // Markdown blob from LLM — render whole thing
    allChildren.push(...mdToParagraphs(spec.execSummary.keyFindingsMd));
  } else if (spec.execSummary.keyFindings && spec.execSummary.keyFindings.length) {
    for (const finding of spec.execSummary.keyFindings) {
      allChildren.push(...mdToParagraphs(`-  ${finding}`));
    }
  }
}

// --- Firm Profile ---
if (spec.firmProfile) {
  allChildren.push(H1('Firm Profile'));
  allChildren.push(H2(`${meta.firmShort} at a Glance`, meta.primaryColorHex));
  for (const item of spec.firmProfile.atAGlance || []) {
    allChildren.push(bullet(item));
  }
  if (spec.firmProfile.ownership) {
    allChildren.push(H2('Ownership & Historical Evolution', meta.primaryColorHex));
    allChildren.push(...mdToParagraphs(spec.firmProfile.ownership));
  }
  if (spec.firmProfile.acquisitions) {
    allChildren.push(H2('Acquisition History', meta.primaryColorHex));
    allChildren.push(...mdToParagraphs(spec.firmProfile.acquisitions));
  }
}

// --- Methodology ---
if (spec.methodology) {
  allChildren.push(H1('Methodology & Data Sources'));
  allChildren.push(...mdToParagraphs(spec.methodology));
}

// --- Sections (12 sectors) ---
for (const section of sections) {
  allChildren.push(H1(`${section.sectionNum}. ${section.sectorLabel}`, '#' + section.primaryColorHex));
  allChildren.push(H2(section.sectorLabel, '#' + section.primaryColorHex));

  if (section.narrativeMd) {
    allChildren.push(...mdToParagraphs(section.narrativeMd));
  }

  // 5 charts, each preceded by its mini heading
  if (section.charts.nominal) {
    allChildren.push(H3(`${meta.firmShort} vs. ENR Composite — Nominal Revenue`));
    allChildren.push(imageParagraph(section.charts.nominal));
  }
  if (section.charts.real) {
    allChildren.push(H3(`${meta.firmShort} vs. ENR Composite — Constant ${meta.baseYear}$ Revenue`));
    allChildren.push(imageParagraph(section.charts.real));
  }
  if (section.charts.yoyNominal) {
    allChildren.push(H3('Year-Over-Year Growth — Nominal'));
    allChildren.push(imageParagraph(section.charts.yoyNominal));
  }
  if (section.charts.yoyReal) {
    allChildren.push(H3(`Year-Over-Year Growth — Real, CCI-Adjusted`));
    allChildren.push(imageParagraph(section.charts.yoyReal));
  }
  if (section.charts.marketShare) {
    allChildren.push(H3(`${meta.firmShort} Market Share of ENR Composite`));
    allChildren.push(imageParagraph(section.charts.marketShare));
  }

  // Comparison table
  allChildren.push(H3(`${meta.firmShort} vs. ENR Composite — Comparison Table`));
  allChildren.push(new Paragraph({
    children: [new TextRun({
      text: `Firm Nominal/Real in $M. ENR Composite in $B. Firm % = ${meta.firmShort} share of composite. YoY Premium = ${meta.firmShort} YoY − ENR Composite YoY (positive = outperformance). Shaded rows = forecast.`,
      italics: true, size: 16, color: '666666', font: 'Calibri',
    })],
    spacing: { before: 60, after: 100 },
  }));
  allChildren.push(comparisonTable(section.rows, '#' + section.primaryColorHex));

  // Page break between sections
  allChildren.push(new Paragraph({ children: [new PageBreak()] }));
}

// --- Strategic Growth Framework ---
if (spec.strategicFramework) {
  allChildren.push(H1('Strategic Growth Framework'));
  allChildren.push(...mdToParagraphs(spec.strategicFramework));
}

// --- Conclusions ---
if (spec.conclusions) {
  allChildren.push(H1('Conclusions'));
  allChildren.push(...mdToParagraphs(spec.conclusions));
}

// --- Build doc ---
const doc = new Document({
  styles: {
    default: { document: { run: { font: 'Calibri', size: 20 } } },
  },
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
            text: `${meta.firmShort} Market Analysis ${meta.startYear}–${meta.endYear} · ${meta.firmShort} vs. ENR Top 500 Composite`,
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


def render_docx(spec: dict, output_path: Path) -> Path:
    """
    Run the JS docx builder with the given spec dict, write to output_path.
    """
    # Stage the JS builder
    builder_js = output_path.parent / "_docx_builder.js"
    builder_js.write_text(DOCX_BUILDER_JS, encoding="utf-8")

    spec_json = json.dumps(spec, default=str)

    proc = subprocess.run(
        ["node", str(builder_js), str(output_path)],
        input=spec_json, text=True,
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docx build failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
    return output_path


def build_spec(
    firm_short: str,
    start_year: int,
    end_year: int,
    base_year: int,
    primary_color_hex: str,
    publish_date: str,
    sources: list[str],
    sections_data: list[dict],   # output of serialize_section()
    exec_summary: Optional[dict] = None,
    firm_profile: Optional[dict] = None,
    methodology: Optional[str] = None,
    strategic_framework: Optional[str] = None,
    conclusions: Optional[str] = None,
) -> dict:
    return {
        "meta": {
            "firmShort": firm_short,
            "startYear": start_year,
            "endYear": end_year,
            "baseYear": base_year,
            "primaryColorHex": primary_color_hex,
            "publishDate": publish_date,
            "sources": sources,
        },
        "execSummary": exec_summary,
        "firmProfile": firm_profile,
        "methodology": methodology,
        "sections": sections_data,
        "strategicFramework": strategic_framework,
        "conclusions": conclusions,
    }


if __name__ == "__main__":
    # Smoke test: render the Power section as a single-section doc
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(HERE / "lib"))
    from ingest import build_panel, load_cci_annual
    from resolve import get_firm_panel, resolve as resolve_fn
    from charts import build_composite_by_year, render_sector_charts
    from compute import compute_section_facts

    panel = build_panel(HERE / "data" / "enr")
    cci = load_cci_annual(HERE / "data" / "cci.xlsx", base_year=2025)
    cci_lookup = dict(zip(cci["year"], cci["deflator"]))
    composite = build_composite_by_year(panel)

    match = resolve_fn(panel, "HDR", interactive=False)
    firm_data = get_firm_panel(panel, match)

    facts = compute_section_facts(
        firm_data=firm_data, composite_by_year=composite,
        sector_key="power", sector_label="Power", firm_short="HDR",
        cci_lookup=cci_lookup,
    )

    chart_paths = render_sector_charts(
        firm_data=firm_data, composite_by_year=composite,
        firm_short="HDR", sector_label="Power", sector_key="power",
        last_actual_year=2025, forecast_year=None,
        output_dir=HERE / "output" / "_smoke_charts",
        cci_lookup=cci_lookup,
    )

    # Placeholder narrative since we can't call the API in the sandbox
    placeholder_narrative = (
        f"**[Placeholder narrative — replaced by Anthropic-generated prose at runtime.]**\n\n"
        f"Power is one of {facts.firm_short}'s sectors. Revenue grew from "
        f"{_format_money_short(facts.start_nom_m)} in {facts.start_year} to "
        f"{_format_money_short(facts.end_nom_m)} in {facts.end_year} — a "
        f"{facts.nominal_cagr_pct:.1f}% nominal CAGR vs. the ENR Composite's "
        f"{facts.comp_nominal_cagr_pct:.1f}%. {facts.firm_short}'s share of the composite Power "
        f"market grew from {facts.start_share*100:.2f}% to {facts.end_share*100:.2f}% over "
        f"the period."
    )

    sec = serialize_section(facts, placeholder_narrative, chart_paths, section_num=5)

    spec = build_spec(
        firm_short="HDR",
        start_year=2005, end_year=2025, base_year=2025,
        primary_color_hex="D62828",
        publish_date="April 27, 2026",
        sources=[
            "ENR Top 500 Design Firms surveys (2005–2026 editions)",
            "ENR 20-City CCI (inflation adjustment, 2025 base)",
        ],
        sections_data=[sec],
        methodology=(
            "Firm-level revenue from ENR Top 500 Design Firms editions. "
            "Real values use the ENR 20-City CCI annual averages with 2025 as base. "
            "ENR Composite computed as Σ(firm revenue × firm sector %) across all 500 firms per year."
        ),
    )

    out = HERE / "output" / "smoke_HDR_Power_only.docx"
    render_docx(spec, out)
    print(f"Wrote: {out}")
