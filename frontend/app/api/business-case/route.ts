import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL!;
const BACKEND_API_SECRET = process.env.BACKEND_API_SECRET!;

export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.firmName?.trim()) {
    return NextResponse.json({ error: "firmName is required" }, { status: 400 });
  }

  const sector =
    typeof body.sector === "string" && body.sector.trim() ? body.sector.trim() : null;
  const targetYear =
    typeof body.targetYear === "number" && Number.isFinite(body.targetYear)
      ? body.targetYear
      : null;

  const payload = {
    firm_name: body.firmName.trim(),
    sector,
    target_year: targetYear,
    no_narrative: body.noNarrative ?? false,
    model: body.model ?? "claude-sonnet-4-6",
  };

  const resp = await fetch(`${BACKEND_URL}/generate-business-case`, {
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

  return NextResponse.json(await resp.json());
}
