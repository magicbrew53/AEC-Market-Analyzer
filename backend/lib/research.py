"""
research.py — load per-firm research files.

Don creates one research file per target firm, e.g. data/research/HDR.json. This
file contains the firm-specific facts that ENR data does not provide:

  - At-a-Glance bullet items (HQ, founded, CEO, ownership, employees, offices)
  - Ownership history & evolution narrative
  - Acquisition timeline narrative
  - Strategic growth initiative name / launch date / goals
  - Notable industry awards (optional)
  - Color preference for the firm's branding (optional)

If no research file exists for a firm, the firm-profile and strategic-framework
sections are stubbed with `[DON: research file missing for this firm]`. Don can
still ship the data-driven sections (the 12 sector chapters) and fill these in
manually.

The loader is forgiving — every field is optional. Fields that are missing
become None and the renderer adapts.

Schema (HDR.json example):

{
  "firmShort": "HDR",
  "firmLegalName": "HDR Inc.",
  "primaryColorHex": "D62828",
  "atAGlance": [
    "Headquarters: Omaha, Nebraska",
    "Founded: 1917 (108 years)",
    "CEO: John W. Henderson, PE (appointed January 2024)",
    "Employees: 13,000+ employee-owners worldwide",
    "Offices: 200+ locations in 10 countries",
    "Ownership: 100% employee-owned (ESOP since 1996)"
  ],
  "ownership": "HDR's 108-year history reflects a transformation ...",
  "acquisitions": "HDR has acquired 60+ firms since the 2010s ...",
  "strategicInitiative": {
    "name": "Active Growth scenario (Feb 2026 strategy report)",
    "launchYear": 2026,
    "goals": "Targeting $5.5–6.0B by 2029 via 3–5 acquisitions and 15+ strategic hires",
    "growthOrgLeaders": ["John W. Henderson (CEO)", "Eric L. Keen (Chairman)"]
  },
  "awards": ["..."],
  "fmiTargets": {
    "power": {"share2029": 0.035, "rationale": "..."},
    "transportation": {"share2029": 0.045, "rationale": "..."}
  }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class StrategicInitiative:
    name: Optional[str] = None
    launchYear: Optional[int] = None
    goals: Optional[str] = None
    growthOrgLeaders: list[str] = field(default_factory=list)


@dataclass
class FirmResearch:
    firmShort: str
    firmLegalName: Optional[str] = None
    primaryColorHex: Optional[str] = None
    atAGlance: list[str] = field(default_factory=list)
    ownership: Optional[str] = None
    acquisitions: Optional[str] = None
    strategicInitiative: Optional[StrategicInitiative] = None
    awards: list[str] = field(default_factory=list)
    fmiTargets: dict = field(default_factory=dict)
    notes: Optional[str] = None  # any extra freeform notes for the LLM
    aliasOverrides: list[str] = field(default_factory=list)  # firm_keys to UNION for this firm
    revwinPilot: dict = field(default_factory=dict)  # per-sector pilot assumption overrides

    @property
    def has_profile(self) -> bool:
        return bool(self.atAGlance or self.ownership or self.acquisitions)

    @property
    def has_strategy(self) -> bool:
        return self.strategicInitiative is not None and bool(self.strategicInitiative.name)


def load_research(path: Path | None) -> FirmResearch | None:
    """Load a research file. Returns None if path is None or file is missing."""
    if path is None or not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))

    si = raw.get("strategicInitiative")
    if si:
        si = StrategicInitiative(
            name=si.get("name"),
            launchYear=si.get("launchYear"),
            goals=si.get("goals"),
            growthOrgLeaders=si.get("growthOrgLeaders", []),
        )

    return FirmResearch(
        firmShort=raw.get("firmShort", "UNKNOWN"),
        firmLegalName=raw.get("firmLegalName"),
        primaryColorHex=raw.get("primaryColorHex"),
        atAGlance=raw.get("atAGlance", []),
        ownership=raw.get("ownership"),
        acquisitions=raw.get("acquisitions"),
        strategicInitiative=si,
        awards=raw.get("awards", []),
        fmiTargets=raw.get("fmiTargets", {}),
        notes=raw.get("notes"),
        aliasOverrides=raw.get("aliasOverrides", []),
        revwinPilot=raw.get("revwinPilot", {}),
    )


def write_template_research_file(firm_short: str, out_path: Path) -> None:
    """Write a blank template that Don fills in per firm."""
    template = {
        "firmShort": firm_short,
        "firmLegalName": f"<full legal name, e.g. {firm_short} Inc.>",
        "primaryColorHex": "D62828",
        "_aliasOverrides_comment": (
            "Optional: list of canonical ENR firm_keys to UNION for this firm. "
            "Use this if you want to override the built-in alias map. "
            "E.g., to include WS Atkins history under AtkinsRealis, set "
            "aliasOverrides to ['ATKINSREALIS', 'SNC LAVALIN', 'SNC LAVALIN GROUP', "
            "'ATKINS NORTH AMERICA']. Leave empty array to use built-in aliases."
        ),
        "aliasOverrides": [],
        "atAGlance": [
            "Headquarters: <city, state>",
            "Founded: <year>",
            "CEO: <name, title>",
            "Employees: <count>",
            "Offices: <count, geographic spread>",
            "Ownership: <ESOP / Private / PE-Backed / Public>",
        ],
        "ownership": "<2-4 paragraph history of the firm's ownership transitions and key leadership eras>",
        "acquisitions": "<2-4 paragraph summary of M&A history; can also include a markdown table>",
        "strategicInitiative": {
            "name": "<branded growth initiative name, e.g. 'Transform & Grow', 'Active Growth'>",
            "launchYear": 2025,
            "goals": "<stated public goals, e.g. 'Top-15 ENR rank by 2030, $5B revenue'>",
            "growthOrgLeaders": ["<CEO name>", "<CGO name>"],
        },
        "awards": [
            "<Year — Award name — Awarding body>",
        ],
        "fmiTargets": {
            "power": {"share2029": None, "rationale": "<what the strategy report claims>"},
            "transportation": {"share2029": None, "rationale": "<what the strategy report claims>"}
        },
        "notes": "<any other freeform context the LLM should know about this firm>",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, indent=2), encoding="utf-8")


if __name__ == "__main__":
    # Smoke test: write a template, then re-load it
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "TEST.json"
        write_template_research_file("TEST", p)
        r = load_research(p)
        print(f"Loaded: {r.firmShort}, has_profile={r.has_profile}, has_strategy={r.has_strategy}")
        print(f"  At-a-Glance items: {len(r.atAGlance)}")
        print(f"  Strategic initiative name: {r.strategicInitiative.name if r.strategicInitiative else None}")
