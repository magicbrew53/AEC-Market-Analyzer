import os
import uuid
import logging
import threading
import traceback
from datetime import datetime, timezone
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

app = FastAPI(title="RevWin Market Analysis API")


@app.on_event("startup")
def startup_sync_data():
    """Download data files from Vercel Blob on every cold start."""
    try:
        from lib.data_loader import ensure_data
        ensure_data(DATA_DIR)
    except Exception as exc:
        logging.warning("Data sync failed (will use local cache if available): %s", exc)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ["DATABASE_URL"]
API_SECRET = os.environ.get("BACKEND_API_SECRET", "")


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def require_auth(x_api_secret: Optional[str] = Header(None)):
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


def run_pipeline(job_id: str, req: GenerateRequest):
    try:
        update_job(job_id, status="running", message="Loading ENR data...", progress=5)

        # Import here so Railway startup doesn't fail if data files are missing
        import sys
        sys.path.insert(0, os.path.dirname(__file__))

        from lib.ingest import load_panel
        from lib.resolve import resolve_firm
        from lib.compute import compute_all_sections
        from lib.charts import render_all_charts
        from lib.narrative import generate_narratives
        from lib.research import load_research
        from lib.forecast import load_forecast
        from lib.docx_render import assemble_document

        data_dir = os.path.join(os.path.dirname(__file__), "data")
        output_dir = os.path.join(os.path.dirname(__file__), "output")
        os.makedirs(output_dir, exist_ok=True)

        panel = load_panel(os.path.join(data_dir, "enr"))
        update_job(job_id, progress=15, message="Resolving firm name...")

        firm_keys = resolve_firm(
            req.firm_name,
            panel,
            aliases_path=os.path.join(data_dir, "user_aliases.json"),
            non_interactive=True,
        )
        if not firm_keys:
            update_job(job_id, status="failed", message=f"Firm '{req.firm_name}' not found in ENR data.")
            return

        update_job(job_id, progress=25, message="Computing section facts...")

        cci_path = os.path.join(data_dir, "cci.xlsx")
        fmi_path = os.path.join(data_dir, "fmi_forecast.json")
        research = load_research(req.firm_name, os.path.join(data_dir, "research"))
        forecast = load_forecast(fmi_path) if not req.no_forecast else {}

        sections = compute_all_sections(
            panel,
            firm_keys,
            cci_path=cci_path,
            span=(req.span_start, req.span_end),
            base_year=req.base_year,
            forecast=forecast,
        )
        update_job(job_id, progress=40, message="Rendering charts...")

        chart_dir = os.path.join(output_dir, f"_{job_id}_charts")
        os.makedirs(chart_dir, exist_ok=True)
        chart_paths = render_all_charts(sections, chart_dir, research=research)

        update_job(job_id, progress=65, message="Generating narratives...")

        narratives = {}
        if not req.no_narrative:
            narratives = generate_narratives(
                sections,
                research=research,
                model=req.model,
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            )

        update_job(job_id, progress=85, message="Assembling document...")

        slug = req.firm_name.replace(" ", "_").upper()
        date_str = datetime.now(timezone.utc).strftime("%Y_%m_%d")
        filename = f"{slug}_Market_Analysis_{date_str}.docx"
        out_path = os.path.join(output_dir, filename)

        assemble_document(
            out_path,
            sections=sections,
            narratives=narratives,
            chart_paths=chart_paths,
            research=research,
            firm_name=req.firm_name,
            span=(req.span_start, req.span_end),
            base_year=req.base_year,
        )

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

    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            message="Pipeline error.",
            error=traceback.format_exc()[:2000],
        )


def upload_to_blob(file_path: str, filename: str) -> str:
    """Upload file to Vercel Blob and return public URL."""
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
