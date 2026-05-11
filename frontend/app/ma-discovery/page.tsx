"use client";

import { useState } from "react";

interface Acquisition {
  acquired_firm_display: string;
  firm_keys: string[];
  acquisition_year: number;
  last_pre_merger_year: number;
  source_url: string;
  confidence: string;
  needs_review: boolean;
  enabled: boolean;
}

interface DiscoveryResponse {
  firm_short: string;
  cached: boolean;
  cached_at: string;
  candidates_proposed?: number;
  acquisitions: Acquisition[];
}

export default function MADiscoveryTest() {
  const [firmName, setFirmName] = useState("");
  const [refresh, setRefresh] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DiscoveryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!firmName.trim()) return;
    setLoading(true);
    setResult(null);
    setError(null);

    try {
      const res = await fetch("/api/ma-discovery", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firmName, refresh }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Unknown error");
      setResult(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <header>
        <h1>M&amp;A Discovery — Test Page</h1>
        <span>Layer 1 verification: Haiku + web_search → ENR-verified acquisitions</span>
      </header>

      <main>
        <div className="card">
          <form onSubmit={handleSubmit}>
            <div>
              <label htmlFor="firmName">Firm name</label>
              <input
                id="firmName"
                type="text"
                placeholder='e.g. "AECOM", "HDR", "Stantec"'
                value={firmName}
                onChange={(e) => setFirmName(e.target.value)}
                disabled={loading}
                autoFocus
              />
            </div>
            <div className="checkbox-row">
              <label>
                <input
                  type="checkbox"
                  checked={refresh}
                  onChange={(e) => setRefresh(e.target.checked)}
                  disabled={loading}
                />
                Force refresh (ignore cache, re-call Haiku — costs ~$0.02)
              </label>
            </div>
            <button className="primary" type="submit" disabled={loading || !firmName.trim()}>
              {loading ? "Discovering… (web search can take ~30s)" : "Discover M&A history"}
            </button>
            <div style={{ marginTop: "0.5rem", fontSize: "0.85rem", color: "#666" }}>
              Calls Claude Haiku with web search, verifies each acquisition against the ENR panel, writes the result to data/ma_cache/{"{FIRM}"}.json on the backend.
            </div>
          </form>
        </div>

        {error && (
          <div className="status-card">
            <div className="status-header">
              <strong>Discovery failed</strong>
              <span className="status-label status-failed">Error</span>
            </div>
            <div className="error-box">{error}</div>
          </div>
        )}

        {result && (
          <div className="status-card">
            <div className="status-header">
              <div>
                <strong>{result.firm_short}</strong>
                <div className="history-meta" style={{ marginTop: 2 }}>
                  {result.cached ? "From cache" : "Fresh from Haiku"}
                  {result.candidates_proposed != null && (
                    <> · Haiku proposed {result.candidates_proposed}; verified {result.acquisitions.length}</>
                  )}
                  {" · "}cached_at {new Date(result.cached_at).toLocaleString()}
                </div>
              </div>
              <span className="status-label status-complete">Ready</span>
            </div>

            {result.acquisitions.length === 0 ? (
              <div style={{ padding: "1rem", color: "#888", fontStyle: "italic" }}>
                No qualifying ENR-listed acquisitions found for {result.firm_short}.
              </div>
            ) : (
              <table style={{ width: "100%", marginTop: "0.5rem", borderCollapse: "collapse", fontSize: "0.9rem" }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid #ddd", textAlign: "left" }}>
                    <th style={{ padding: "0.5rem" }}>Year</th>
                    <th style={{ padding: "0.5rem" }}>Acquired firm</th>
                    <th style={{ padding: "0.5rem" }}>firm_keys</th>
                    <th style={{ padding: "0.5rem" }}>Conf</th>
                    <th style={{ padding: "0.5rem" }}>Review?</th>
                    <th style={{ padding: "0.5rem" }}>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {result.acquisitions
                    .slice()
                    .sort((a, b) => a.acquisition_year - b.acquisition_year)
                    .map((a, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                        <td style={{ padding: "0.5rem", fontWeight: 600 }}>{a.acquisition_year}</td>
                        <td style={{ padding: "0.5rem" }}>{a.acquired_firm_display}</td>
                        <td style={{ padding: "0.5rem", fontFamily: "monospace", fontSize: "0.8rem", color: "#666" }}>
                          {a.firm_keys.join(", ")}
                        </td>
                        <td style={{ padding: "0.5rem" }}>
                          <span style={{
                            padding: "2px 8px",
                            borderRadius: "10px",
                            fontSize: "0.75rem",
                            background: a.confidence === "high" ? "#d4edda" : "#fff3cd",
                            color: a.confidence === "high" ? "#155724" : "#856404",
                          }}>
                            {a.confidence}
                          </span>
                        </td>
                        <td style={{ padding: "0.5rem", color: a.needs_review ? "#b83227" : "#888" }}>
                          {a.needs_review ? "yes" : "—"}
                        </td>
                        <td style={{ padding: "0.5rem" }}>
                          <a href={a.source_url} target="_blank" rel="noopener noreferrer" style={{ color: "#1F6FB4" }}>
                            link
                          </a>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </main>
    </>
  );
}
