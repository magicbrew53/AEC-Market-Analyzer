import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL!;
const BACKEND_API_SECRET = process.env.BACKEND_API_SECRET!;

export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.firmName?.trim()) {
    return NextResponse.json({ error: "firmName is required" }, { status: 400 });
  }

  const resp = await fetch(`${BACKEND_URL}/discover-ma`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-secret": BACKEND_API_SECRET,
    },
    body: JSON.stringify({
      firm_name: body.firmName.trim(),
      refresh: !!body.refresh,
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    return NextResponse.json({ error: text }, { status: resp.status });
  }

  return NextResponse.json(await resp.json());
}
