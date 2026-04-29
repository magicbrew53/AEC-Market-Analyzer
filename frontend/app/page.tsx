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
}

const POLL_INTERVAL = 4000;

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-label status-${status}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function JobStatusCard({ job }: { job: Job }) {
  return (
    <div className="status-card">
      <div className="status-header">
        <div>
          <strong>{job.firmName}</strong>
          <div className="history-meta" style={{ marginTop: 2 }}>
            {new Date(job.createdAt).toLocaleString()}
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
  const [firmName, setFirmName] = useState("");
  const [spanStart, setSpanStart] = useState(2005);
  const [spanEnd, setSpanEnd] = useState(2025);
  const [baseYear, setBaseYear] = useState(2025);
  const [noNarrative, setNoNarrative] = useState(false);
  const [noForecast, setNoForecast] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load history from localStorage on mount
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

  function startPolling(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job: Job = await res.json();
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

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmName, spanStart, spanEnd, baseYear, noNarrative, noForecast }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Unknown error");

      const job: Job = {
        id: data.jobId,
        firmName,
        status: "pending",
        progress: 0,
        message: "Queued...",
        createdAt: new Date().toISOString(),
      };
      setActiveJob(job);
      saveToHistory(job);
      startPolling(data.jobId);
    } catch (err: unknown) {
      setLoading(false);
      setActiveJob({
        id: "error",
        firmName,
        status: "failed",
        progress: 0,
        message: "Failed to start job.",
        error: String(err),
        createdAt: new Date().toISOString(),
      });
    }
  }

  // Resume polling for any in-progress job from history on load
  useEffect(() => {
    const inProgress = history.find(
      (j) => j.status === "pending" || j.status === "running"
    );
    if (inProgress && !pollRef.current) {
      setActiveJob(inProgress);
      setLoading(true);
      startPolling(inProgress.id);
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

            <button className="primary" type="submit" disabled={loading || !firmName.trim()}>
              {loading ? "Generating…" : "Generate Report"}
            </button>
          </form>
        </div>

        {activeJob && <JobStatusCard job={activeJob} />}

        {completedHistory.length > 0 && (
          <div className="history">
            <h2>Recent Reports</h2>
            {completedHistory.map((j) => (
              <div className="history-item" key={j.id}>
                <div>
                  <div className="history-firm">{j.firmName}</div>
                  <div className="history-meta">{new Date(j.createdAt).toLocaleString()}</div>
                </div>
                <div style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}>
                  {j.downloadUrl && (
                    <a className="history-link" href={j.downloadUrl} download={j.filename}>
                      Download
                    </a>
                  )}
                  <button
                    onClick={() => deleteFromHistory(j.id)}
                    style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, cursor: "pointer", color: "var(--muted)", fontSize: "0.75rem", padding: "2px 7px", lineHeight: 1.4 }}
                    title="Remove from history"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </>
  );
}
