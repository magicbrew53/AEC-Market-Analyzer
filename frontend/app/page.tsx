"use client";

import { useState, useEffect, useRef } from "react";

interface Job {
  id: string;
  firmName: string;
  status: string;
  progress: number;
  message?: string;
  downloadUrl?: string;
  filename?: string;
  error?: string;
  createdAt: string;
  reportType?: ReportType;
}

type ReportType = "market-analysis" | "business-case";

const POLL_INTERVAL = 4000;

const SECTOR_OPTIONS: { value: string; label: string }[] = [
  { value: "",                            label: "Auto-pick (flattest vs. composite)" },
  { value: "water_supply,sewer_waste",    label: "Water (combined: Water Supply + Sewer/Waste)" },
  { value: "gen_bldg,manufacturing",      label: "Building & Industrial (General Building + Manufacturing)" },
  { value: "water_supply",                label: "Water Supply" },
  { value: "sewer_waste",                 label: "Sewer/Waste Water" },
  { value: "gen_bldg",                    label: "General Building" },
  { value: "manufacturing",               label: "Manufacturing" },
  { value: "power",                       label: "Power" },
  { value: "transportation",              label: "Transportation" },
  { value: "ind_pet",                     label: "Industrial/Petroleum" },
  { value: "haz_waste",                   label: "Hazardous Waste" },
  { value: "telecom",                     label: "Telecommunications" },
  { value: "other",                       label: "Other" },
];

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-label status-${status}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function JobStatusCard({ job }: { job: Job }) {
  const reportLabel =
    job.reportType === "business-case" ? "RevWin Business Case" : "Market Analysis";
  return (
    <div className="status-card">
      <div className="status-header">
        <div>
          <strong>{job.firmName}</strong>
          <div className="history-meta" style={{ marginTop: 2 }}>
            {reportLabel} · {new Date(job.createdAt).toLocaleString()}
          </div>
        </div>
        <StatusBadge status={job.status} />
      </div>

      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${job.progress}%` }} />
      </div>

      <div className="status-message">{job.message ?? "—"}</div>

      {job.status === "failed" && job.error && (
        <div className="error-box">{job.error}</div>
      )}

      {job.status === "complete" && job.downloadUrl && (
        <a className="download" href={job.downloadUrl} download={job.filename}>
          Download Report (.docx)
        </a>
      )}
    </div>
  );
}

export default function Home() {
  const [reportType, setReportType] = useState<ReportType>("market-analysis");
  const [firmName, setFirmName] = useState("");

  // Market Analysis options
  const [spanStart, setSpanStart] = useState(2005);
  const [spanEnd, setSpanEnd] = useState(2025);
  const [baseYear, setBaseYear] = useState(2025);
  const [noNarrative, setNoNarrative] = useState(false);
  const [noForecast, setNoForecast] = useState(false);

  // Business Case options
  const [bcSector, setBcSector] = useState<string>(""); // "" = auto-pick
  const [bcTargetYear, setBcTargetYear] = useState<string>(""); // empty = use research file or default

  const [loading, setLoading] = useState(false);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("rwa_history") ?? "[]");
      setHistory(saved);
    } catch {}
  }, []);

  function saveToHistory(job: Job) {
    setHistory((prev) => {
      const next = [job, ...prev.filter((j) => j.id !== job.id)].slice(0, 20);
      localStorage.setItem("rwa_history", JSON.stringify(next));
      return next;
    });
  }

  function startPolling(jobId: string, type: ReportType) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job: Job = await res.json();
        job.reportType = type;
        setActiveJob(job);
        saveToHistory(job);
        if (job.status === "complete" || job.status === "failed") {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setLoading(false);
        }
      } catch {}
    }, POLL_INTERVAL);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!firmName.trim()) return;
    setLoading(true);
    setActiveJob(null);

    const initialMessage =
      reportType === "business-case"
        ? "Picking sector and computing ROI…"
        : "Queued…";

    try {
      let endpoint: string;
      let payload: Record<string, unknown>;

      if (reportType === "business-case") {
        endpoint = "/api/business-case";
        const targetYearNum = bcTargetYear.trim() ? Number(bcTargetYear) : null;
        payload = {
          firmName,
          sector: bcSector || null,
          targetYear: targetYearNum,
          noNarrative,
        };
      } else {
        endpoint = "/api/generate";
        payload = {
          firmName, spanStart, spanEnd, baseYear, noNarrative, noForecast,
        };
      }

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Unknown error");

      const job: Job = {
        id: data.jobId,
        firmName,
        status: "pending",
        progress: 0,
        message: initialMessage,
        createdAt: new Date().toISOString(),
        reportType,
      };
      setActiveJob(job);
      saveToHistory(job);
      startPolling(data.jobId, reportType);
    } catch (err: unknown) {
      setLoading(false);
      // Surface the real backend error — picker may legitimately fail when no
      // sectors pass size/share filters, and the user needs to know to override.
      setActiveJob({
        id: "error",
        firmName,
        status: "failed",
        progress: 0,
        message:
          reportType === "business-case"
            ? "Business case generation failed."
            : "Failed to start job.",
        error: err instanceof Error ? err.message : String(err),
        createdAt: new Date().toISOString(),
        reportType,
      });
    }
  }

  useEffect(() => {
    const inProgress = history.find(
      (j) => j.status === "pending" || j.status === "running"
    );
    if (inProgress && !pollRef.current) {
      setActiveJob(inProgress);
      setLoading(true);
      startPolling(inProgress.id, inProgress.reportType ?? "market-analysis");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function deleteFromHistory(id: string) {
    setHistory((prev) => {
      const next = prev.filter((j) => j.id !== id);
      localStorage.setItem("rwa_history", JSON.stringify(next));
      return next;
    });
  }

  const completedHistory = history.filter(
    (j) => j.status === "complete" && j.id !== activeJob?.id
  );

  const submitLabel =
    reportType === "business-case"
      ? loading ? "Generating Business Case…" : "Generate RevWin Business Case"
      : loading ? "Generating…" : "Generate Market Analysis";

  return (
    <>
      <header>
        <h1>RevWin Market Analysis</h1>
        <span>ENR Top 500 · AEC Firm Reports</span>
      </header>

      <main>
        <div className="card">
          <form onSubmit={handleSubmit}>
            <div>
              <label htmlFor="firmName">Firm Name</label>
              <input
                id="firmName"
                type="text"
                placeholder='e.g. "HDR", "AECOM", "Stantec"'
                value={firmName}
                onChange={(e) => setFirmName(e.target.value)}
                disabled={loading}
                autoFocus
              />
            </div>

            <div style={{ marginTop: "1rem" }}>
              <label>Report Type</label>
              <div
                role="radiogroup"
                aria-label="Report Type"
                style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={reportType === "market-analysis"}
                  onClick={() => setReportType("market-analysis")}
                  disabled={loading}
                  className={reportType === "market-analysis" ? "primary" : ""}
                  style={{ flex: 1 }}
                >
                  Market Analysis (50-page benchmark)
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={reportType === "business-case"}
                  onClick={() => setReportType("business-case")}
                  disabled={loading}
                  className={reportType === "business-case" ? "primary" : ""}
                  style={{ flex: 1 }}
                >
                  RevWin Business Case (4–6 page sales doc)
                </button>
              </div>
            </div>

            {reportType === "market-analysis" && (
              <>
                <div className="options-grid">
                  <div>
                    <label htmlFor="spanStart">Span Start</label>
                    <input
                      id="spanStart"
                      type="number"
                      min={2005}
                      max={spanEnd - 1}
                      value={spanStart}
                      onChange={(e) => setSpanStart(Number(e.target.value))}
                      disabled={loading}
                    />
                  </div>
                  <div>
                    <label htmlFor="spanEnd">Span End</label>
                    <input
                      id="spanEnd"
                      type="number"
                      min={spanStart + 1}
                      max={2025}
                      value={spanEnd}
                      onChange={(e) => setSpanEnd(Number(e.target.value))}
                      disabled={loading}
                    />
                  </div>
                  <div>
                    <label htmlFor="baseYear">Base Year (Real $)</label>
                    <input
                      id="baseYear"
                      type="number"
                      min={2000}
                      max={2030}
                      value={baseYear}
                      onChange={(e) => setBaseYear(Number(e.target.value))}
                      disabled={loading}
                    />
                  </div>
                </div>

                <div className="checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={noNarrative}
                      onChange={(e) => setNoNarrative(e.target.checked)}
                      disabled={loading}
                    />
                    Skip AI narratives (faster)
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={noForecast}
                      onChange={(e) => setNoForecast(e.target.checked)}
                      disabled={loading}
                    />
                    Skip forecast row
                  </label>
                </div>
              </>
            )}

            {reportType === "business-case" && (
              <>
                <div className="options-grid">
                  <div style={{ gridColumn: "1 / span 2" }}>
                    <label htmlFor="bcSector">Sector override</label>
                    <select
                      id="bcSector"
                      value={bcSector}
                      onChange={(e) => setBcSector(e.target.value)}
                      disabled={loading}
                    >
                      {SECTOR_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="bcTargetYear">Target year (optional)</label>
                    <input
                      id="bcTargetYear"
                      type="number"
                      min={2026}
                      max={2040}
                      placeholder="default: 2029"
                      value={bcTargetYear}
                      onChange={(e) => setBcTargetYear(e.target.value)}
                      disabled={loading}
                    />
                  </div>
                </div>

                <div className="checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={noNarrative}
                      onChange={(e) => setNoNarrative(e.target.checked)}
                      disabled={loading}
                    />
                    Skip AI narratives (faster, free)
                  </label>
                </div>
              </>
            )}

            <button className="primary" type="submit" disabled={loading || !firmName.trim()}>
              {submitLabel}
            </button>

            {reportType === "business-case" && !noNarrative && (
              <div
                style={{
                  marginTop: "0.5rem", fontSize: "0.85rem", color: "#666",
                }}
              >
                ~$0.06 in API costs per report (3 LLM calls).
              </div>
            )}
          </form>
        </div>

        {activeJob && <JobStatusCard job={activeJob} />}

        {completedHistory.length > 0 && (
          <div className="history">
            <h2>Recent Reports</h2>
            {completedHistory.map((j) => (
              <div className="history-item" key={j.id}>
                <div style={{ flex: 1 }}>
                  <div className="history-firm">
                    {j.firmName}
                    {j.reportType === "business-case" && (
                      <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", color: "#888" }}>
                        Business Case
                      </span>
                    )}
                  </div>
                  <div className="history-meta">{new Date(j.createdAt).toLocaleString()}</div>
                </div>
                {j.downloadUrl && (
                  <a className="history-link" href={j.downloadUrl} download={j.filename} style={{ marginRight: "1rem" }}>
                    Download
                  </a>
                )}
                <a
                  className="history-link"
                  onClick={() => deleteFromHistory(j.id)}
                  style={{ cursor: "pointer", color: "var(--danger)" }}
                >
                  Delete
                </a>
              </div>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
