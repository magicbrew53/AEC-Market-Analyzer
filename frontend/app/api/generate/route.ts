import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL!;
const BACKEND_API_SECRET = process.env.BACKEND_API_SECRET!;

export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.firmName?.trim()) {
    return NextResponse.json({ error: "firmName is required" }, { status: 400 });
  }

  const payload = {
    firm_name: body.firmName.trim(),
    span_start: body.spanStart ?? 2005,
    span_end: body.spanEnd ?? 2025,
    base_year: body.baseYear ?? 2025,
    no_narrative: body.noNarrative ?? false,
    no_forecast: body.noForecast ?? false,
    model: body.model ?? "claude-sonnet-4-6",
  };

  const resp = await fetch(`${BACKEND_URL}/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-secret": BACKEND_API_SECRET,
    },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const text = await resp.text();
    return NextResponse.json({ error: text }, { status: resp.status });
  }

  const data = await resp.json();
  return NextResponse.json(data);
}
