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

const SECTOR_OPTIONS: { value: string; label: string }[] = [
  { value: "",                            label: "Auto-pick (where your firm has the most room to grow)" },
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
  return (
    <div className="status-card">
      <div className="status-header">
        <div>
          <strong>{job.firmName}</strong>
          <div className="history-meta" style={{ marginTop: 2 }}>
            Building your business case…
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
          Download Your Business Case (.docx)
        </a>
      )}
    </div>
  );
}

export default function BusinessCaseBuilder() {
  const [firmName, setFirmName] = useState("");
  const [bcSector, setBcSector] = useState<string>("");
  const [bcTargetYear, setBcTargetYear] = useState<string>("");
  const [bcPilotVolume, setBcPilotVolume] = useState<string>("");
  const [bcWinRateUplift, setBcWinRateUplift] = useState<string>("");
  const [bcPilotDuration, setBcPilotDuration] = useState<string>("");
  const [noNarrative, setNoNarrative] = useState(false);

  const [loading, setLoading] = useState(false);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function startPolling(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job: Job = await res.json();
        setActiveJob(job);
        if (job.status === "complete" || job.status === "failed") {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setLoading(false);
        }
      } catch {}
    }, POLL_INTERVAL);
  }

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!firmName.trim()) return;
    setLoading(true);
    setActiveJob(null);

    const toNum = (s: string) => (s.trim() ? Number(s) : null);

    try {
      const res = await fetch("/api/business-case", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          firmName,
          sector: bcSector || null,
          targetYear: toNum(bcTargetYear),
          pilotVolumeMid: toNum(bcPilotVolume),
          winRateUpliftMidPp: toNum(bcWinRateUplift),
          pilotDurationQuarters: toNum(bcPilotDuration),
          noNarrative,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Unknown error");

      const job: Job = {
        id: data.jobId,
        firmName,
        status: "pending",
        progress: 0,
        message: "Picking sector and computing ROI…",
        createdAt: new Date().toISOString(),
      };
      setActiveJob(job);
      startPolling(data.jobId);
    } catch (err: unknown) {
      setLoading(false);
      setActiveJob({
        id: "error",
        firmName,
        status: "failed",
        progress: 0,
        message: "Couldn't generate your business case.",
        error: err instanceof Error ? err.message : String(err),
        createdAt: new Date().toISOString(),
      });
    }
  }

  return (
    <>
      <header>
        <h1>Build Your RevWin Business Case</h1>
        <span>A 4–6 page custom sales document for your firm</span>
      </header>

      <main>
        <div className="card">
          <form onSubmit={handleSubmit}>
            <div>
              <label htmlFor="firmName">Your firm&apos;s name</label>
              <input
                id="firmName"
                type="text"
                placeholder='e.g. "HDR", "Stantec", "Burns & McDonnell"'
                value={firmName}
                onChange={(e) => setFirmName(e.target.value)}
                disabled={loading}
                autoFocus
              />
            </div>

            <div className="options-grid">
              <div style={{ gridColumn: "1 / span 2" }}>
                <label htmlFor="bcSector">Pilot sector</label>
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
                <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 4 }}>
                  Auto-pick uses your firm&apos;s ENR data to find the sector with the most upside. Or pick one yourself.
                </div>
              </div>
              <div>
                <label htmlFor="bcTargetYear">Target year</label>
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
                <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 4 }}>
                  Year your growth target is measured against.
                </div>
              </div>
            </div>

            <div className="options-grid">
              <div>
                <label htmlFor="bcPilotVolume">Pilot pursuit volume</label>
                <input
                  id="bcPilotVolume"
                  type="number"
                  min={1}
                  max={500}
                  placeholder="auto"
                  value={bcPilotVolume}
                  onChange={(e) => setBcPilotVolume(e.target.value)}
                  disabled={loading}
                  title="Number of pursuits the team will run through the coached capture workflow during the pilot."
                />
                <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 4 }}>
                  How many pursuits run through the pilot. Defaults: Water 75 · Power 60 · Building 90.
                </div>
              </div>
              <div>
                <label htmlFor="bcWinRateUplift">Target win-rate uplift (pp)</label>
                <input
                  id="bcWinRateUplift"
                  type="number"
                  min={0.5}
                  max={50}
                  step={0.5}
                  placeholder="auto"
                  value={bcWinRateUplift}
                  onChange={(e) => setBcWinRateUplift(e.target.value)}
                  disabled={loading}
                  title="Percentage-point improvement in win rate vs. baseline."
                />
                <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 4 }}>
                  Points added to baseline win rate (e.g. 12 = 30% → 42%). Default 12.
                </div>
              </div>
              <div>
                <label htmlFor="bcPilotDuration">Pilot duration (quarters)</label>
                <input
                  id="bcPilotDuration"
                  type="number"
                  min={1}
                  max={12}
                  placeholder="default: 6"
                  value={bcPilotDuration}
                  onChange={(e) => setBcPilotDuration(e.target.value)}
                  disabled={loading}
                  title="Length of the pilot in quarters. Drives the Timeline section in your document."
                />
                <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 4 }}>
                  How long the pilot runs. Default 6 quarters (18 months).
                </div>
              </div>
            </div>

            <div style={{ marginTop: "0.25rem", fontSize: "0.8rem", color: "#888" }}>
              These are the <strong>middle scenario</strong> values — Conservative and Aggressive scenarios in the ROI table scale around them.
            </div>

            <div className="checkbox-row">
              <label>
                <input
                  type="checkbox"
                  checked={noNarrative}
                  onChange={(e) => setNoNarrative(e.target.checked)}
                  disabled={loading}
                />
                Skip AI-written narrative (faster, but the document will have placeholder text)
              </label>
            </div>

            <button className="primary" type="submit" disabled={loading || !firmName.trim()}>
              {loading ? "Building your business case…" : "Build My Business Case"}
            </button>
          </form>
        </div>

        {activeJob && <JobStatusCard job={activeJob} />}
      </main>
    </>
  );
}
