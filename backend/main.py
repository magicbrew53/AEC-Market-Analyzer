import os
import uuid
import logging
import threading
import traceback
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"

app = FastAPI(title="RevWin Market Analysis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ["DATABASE_URL"]
API_SECRET = os.environ.get("BACKEND_API_SECRET", "")


@app.on_event("startup")
def startup_sync_data():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from lib.data_loader import ensure_data
        ensure_data(DATA_DIR)
    except Exception as exc:
        logging.warning("Data sync failed (will use local cache if available): %s", exc)


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def require_auth(x_api_secret: Optional[str]):
    if API_SECRET and x_api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


class GenerateRequest(BaseModel):
    firm_name: str
    span_start: int = 2005
    span_end: int = 2025
    base_year: int = 2025
    no_narrative: bool = False
    no_forecast: bool = False
    model: str = "claude-sonnet-4-6"


def update_job(job_id: str, **kwargs):
    fields = ", ".join(f'"{k}" = %s' for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'UPDATE "Job" SET {fields}, "updatedAt" = NOW() WHERE id = %s',
                values,
            )
        conn.commit()


def make_placeholder_narrative(facts) -> str:
    from lib.docx_render import _format_money_short
    if facts.start_nom_m and facts.end_nom_m and facts.start_share and facts.end_share:
        return (
            f"**[Placeholder narrative — replaced by LLM-generated prose when "
            f"ANTHROPIC_API_KEY is set.]**\n\n"
            f"{facts.firm_short}'s {facts.sector_label} revenue grew from "
            f"{_format_money_short(facts.start_nom_m)} in {facts.start_year} to "
            f"{_format_money_short(facts.end_nom_m)} in {facts.end_year} — a "
            f"{facts.nominal_cagr_pct:.1f}% nominal CAGR vs. the ENR Composite's "
            f"{facts.comp_nominal_cagr_pct:.1f}%. Share grew from "
            f"{facts.start_share*100:.2f}% to {facts.end_share*100:.2f}% over the period."
        )
    return f"**[Placeholder — facts incomplete for {facts.sector_label}]**"


def run_pipeline(job_id: str, req: GenerateRequest):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, str(Path(__file__).parent / "lib"))

    try:
        update_job(job_id, status="running", message="Loading ENR data...", progress=5)

        import pandas as pd
        from lib.ingest import build_panel, load_cci_annual
        from lib.resolve import resolve as resolve_fn, get_firm_panel
        from lib.charts import build_composite_by_year, render_sector_charts
        from lib.compute import compute_section_facts
        from lib.research import load_research
        from lib.forecast import load_fmi_forecast, project_next_year_value
        from lib.docx_render import (
            SECTION_ORDER, SECTOR_PRIMARY_HEX,
            serialize_section, build_spec, render_docx, _format_money_short,
        )

        start_year, end_year = req.span_start, req.span_end
        base_year = req.base_year

        # --- Load data ---
        panel = build_panel(DATA_DIR / "enr")
        update_job(job_id, progress=12, message="Loading CCI and computing composite...")

        cci = load_cci_annual(DATA_DIR / "cci.xlsx", base_year=base_year)
        cci_lookup = dict(zip(cci["year"], cci["deflator"]))
        composite_by_year = build_composite_by_year(panel)

        # --- Resolve firm ---
        update_job(job_id, progress=18, message=f"Resolving firm '{req.firm_name}'...")
        try:
            match = resolve_fn(
                panel, req.firm_name,
                user_cache_path=DATA_DIR / "user_aliases.json",
                interactive=False,
            )
        except ValueError:
            update_job(job_id, status="failed", message=f"Firm '{req.firm_name}' not found in ENR data.")
            return

        firm_data = get_firm_panel(panel, match)
        if firm_data.empty:
            update_job(job_id, status="failed", message=f"No data rows found for '{req.firm_name}'.")
            return

        actual_start = max(start_year, int(firm_data["data_year"].min()))
        actual_end = min(end_year, int(firm_data["data_year"].max()))
        firm_short = match.firm_keys[0] if match.firm_keys else req.firm_name.upper()

        # --- Load research and FMI ---
        research = load_research(DATA_DIR / "research" / f"{firm_short}.json")
        fmi = None
        if not req.no_forecast:
            fmi = load_fmi_forecast(DATA_DIR / "fmi_forecast.json")
        forecast_year = fmi.forecastYear if (fmi and not req.no_forecast) else None

        # --- Compute section facts ---
        update_job(job_id, progress=28, message="Computing section facts (12 sections)...")
        section_facts = []
        for sector_key, sector_label in SECTION_ORDER:
            try:
                facts = compute_section_facts(
                    firm_data=firm_data, composite_by_year=composite_by_year,
                    sector_key=sector_key, sector_label=sector_label,
                    firm_short=firm_short, cci_lookup=cci_lookup,
                    base_year=base_year, start_year=actual_start, end_year=actual_end,
                    fmi_forecast=fmi,
                )
                section_facts.append((sector_key, sector_label, facts))
            except Exception as e:
                logging.warning("Section facts failed for %s: %s", sector_label, e)

        # --- Render charts ---
        update_job(job_id, progress=40, message="Rendering charts (60 total)...")
        chart_dir = OUTPUT_DIR / f"_{job_id}_charts"
        chart_dir.mkdir(exist_ok=True)
        section_chart_paths = {}
        for sector_key, sector_label, facts in section_facts:
            paths = render_sector_charts(
                firm_data=firm_data, composite_by_year=composite_by_year,
                firm_short=firm_short, sector_label=sector_label, sector_key=sector_key,
                last_actual_year=actual_end, forecast_year=forecast_year,
                output_dir=chart_dir, cci_lookup=cci_lookup,
                fmi_forecast=fmi,
            )
            section_chart_paths[sector_key] = paths

        # --- Narratives ---
        use_llm = (not req.no_narrative) and bool(os.environ.get("ANTHROPIC_API_KEY"))
        update_job(job_id, progress=65, message="Generating narratives..." if use_llm else "Building document...")

        if use_llm:
            from lib.narrative import (
                render_sector_narrative, render_exec_summary_findings,
                render_firm_profile_ownership, render_strategic_framework, render_conclusions,
            )

        sector_narratives = {}
        for sector_key, sector_label, facts in section_facts:
            if use_llm:
                try:
                    sector_narratives[sector_key] = render_sector_narrative(facts, model=req.model)
                except Exception as e:
                    logging.warning("LLM error for %s: %s", sector_label, e)
                    sector_narratives[sector_key] = make_placeholder_narrative(facts)
            else:
                sector_narratives[sector_key] = make_placeholder_narrative(facts)

        facts_only = [f for _, _, f in section_facts]
        total_facts_obj = next((f for k, _, f in section_facts if k == "total"), None)

        if use_llm:
            try:
                exec_findings_md = render_exec_summary_findings(facts_only, firm_short, model=req.model)
            except Exception:
                exec_findings_md = "[LLM error generating Key Findings]"
            try:
                firm_profile_ownership_md = render_firm_profile_ownership(
                    research, total_facts_obj, firm_short, model=req.model)
            except Exception:
                firm_profile_ownership_md = "[LLM error generating firm profile]"
            try:
                sector_projections = []
                for sector_key, sector_label, facts in section_facts:
                    if sector_key in ("total", "intl"):
                        continue
                    sec_series = pd.Series(
                        {r.year: r.firm_nom_m for r in facts.rows if r.firm_nom_m is not None}
                    )
                    proj = project_next_year_value(sec_series, actual_end, sector_key, fmi)
                    sector_projections.append({
                        "sector": sector_label,
                        "current_revenue_m": facts.end_nom_m,
                        "current_share_pct": (facts.end_share or 0) * 100,
                        "projected_baseline_m": proj,
                        "projected_baseline_yoy_pct": (
                            ((proj / facts.end_nom_m - 1) * 100)
                            if proj and facts.end_nom_m else None
                        ),
                    })
                strategic_md = render_strategic_framework(
                    research, sector_projections,
                    fmi.source if fmi else "no FMI source",
                    firm_short, model=req.model,
                )
            except Exception:
                strategic_md = "[LLM error generating strategic framework]"
            try:
                conclusions_md = render_conclusions(facts_only, firm_short, model=req.model)
            except Exception:
                conclusions_md = "[LLM error generating conclusions]"
        else:
            exec_findings_md = (
                f"**[Placeholder — Executive Summary will be generated by LLM at runtime.]**\n\n"
                f"This report covers {firm_short}'s revenue and sector trajectory from "
                f"{actual_start} to {actual_end}, benchmarked against the ENR Top 500 Composite."
            )
            firm_profile_ownership_md = "**[Placeholder — Firm Profile narrative requires the LLM and a research file.]**"
            strategic_md = "**[Placeholder — Strategic Growth Framework requires the LLM.]**"
            conclusions_md = "**[Placeholder — Conclusions section requires the LLM.]**"

        # --- Assemble document ---
        update_job(job_id, progress=88, message="Assembling Word document...")
        primary_color = (research.primaryColorHex if research and research.primaryColorHex
                         else SECTOR_PRIMARY_HEX["total"])

        sections_data = []
        for i, (sector_key, sector_label, facts) in enumerate(section_facts, start=1):
            sections_data.append(serialize_section(
                facts=facts,
                narrative_md=sector_narratives.get(sector_key, ""),
                chart_paths=section_chart_paths.get(sector_key, {}),
                section_num=i,
            ))

        firm_profile = None
        if research:
            firm_profile = {
                "atAGlance": research.atAGlance,
                "ownership": firm_profile_ownership_md if firm_profile_ownership_md else research.ownership,
                "acquisitions": research.acquisitions,
            }

        methodology = (
            f"This report blends {firm_short} firm-level revenue data from "
            f"{actual_end - actual_start + 1} consecutive ENR Top 500 Design Firms editions "
            f"({actual_start}–{actual_end}) with sector-level composite benchmarks. Analysis is "
            f"presented in both nominal dollars and constant {base_year} dollars using the ENR "
            f"20-City Construction Cost Index. "
            + (f"Forecast values for {forecast_year} apply FMI quarterly growth rates per sector."
               if forecast_year else "")
        )

        spec = build_spec(
            firm_short=firm_short,
            start_year=actual_start, end_year=actual_end, base_year=base_year,
            primary_color_hex=primary_color,
            publish_date=date.today().strftime("%B %d, %Y"),
            sources=[
                f"ENR Top 500 Design Firms surveys ({actual_start}–{actual_end + 1} editions)",
                f"ENR 20-City CCI (inflation adjustment, {base_year} base)",
                *(["FMI Q1 forecast (sector growth rates)"] if fmi else []),
            ],
            sections_data=sections_data,
            exec_summary={"keyFindingsMd": exec_findings_md} if exec_findings_md else None,
            firm_profile=firm_profile,
            methodology=methodology,
            strategic_framework=strategic_md,
            conclusions=conclusions_md,
        )

        filename = f"{firm_short}_Market_Analysis_{date.today().strftime('%Y_%m_%d')}.docx"
        out_path = OUTPUT_DIR / filename
        render_docx(spec, out_path)

        # Upload to Vercel Blob
        download_url = upload_to_blob(out_path, filename)

        # Cleanup temp charts
        import shutil
        shutil.rmtree(chart_dir, ignore_errors=True)

        update_job(
            job_id,
            status="complete",
            progress=100,
            message="Report ready.",
            downloadUrl=download_url,
            filename=filename,
        )

    except Exception:
        update_job(
            job_id,
            status="failed",
            message="Pipeline error.",
            error=traceback.format_exc()[:2000],
        )


def upload_to_blob(file_path: Path, filename: str) -> str:
    import requests
    blob_token = os.environ["BLOB_READ_WRITE_TOKEN"]
    with open(file_path, "rb") as f:
        resp = requests.put(
            f"https://blob.vercel-storage.com/{filename}",
            headers={
                "Authorization": f"Bearer {blob_token}",
                "x-content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "x-add-random-suffix": "1",
            },
            data=f,
        )
    resp.raise_for_status()
    return resp.json()["url"]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate")
def generate(req: GenerateRequest, x_api_secret: Optional[str] = Header(None)):
    require_auth(x_api_secret)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''INSERT INTO "Job" (id, "firmName", status, progress, message, "createdAt", "updatedAt")
                   VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (job_id, req.firm_name, "pending", 0, "Queued...", now, now),
            )
        conn.commit()
    thread = threading.Thread(target=run_pipeline, args=(job_id, req), daemon=True)
    thread.start()
    return {"jobId": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_api_secret: Optional[str] = Header(None)):
    require_auth(x_api_secret)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM "Job" WHERE id = %s', (job_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row)
