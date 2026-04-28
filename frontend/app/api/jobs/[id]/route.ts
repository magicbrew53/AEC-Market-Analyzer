import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL!;
const BACKEND_API_SECRET = process.env.BACKEND_API_SECRET!;

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  const resp = await fetch(`${BACKEND_URL}/jobs/${id}`, {
    headers: { "x-api-secret": BACKEND_API_SECRET },
    cache: "no-store",
  });

  if (!resp.ok) {
    return NextResponse.json({ error: "Job not found" }, { status: resp.status });
  }

  return NextResponse.json(await resp.json());
}
