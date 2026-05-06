import { NextRequest, NextResponse } from "next/server";

const PASSWORD = process.env.APP_PASSWORD ?? "";

export function middleware(req: NextRequest) {
  const auth = req.headers.get("authorization") ?? "";
  const [scheme, encoded] = auth.split(" ");

  if (scheme === "Basic" && encoded) {
    const decoded = Buffer.from(encoded, "base64").toString("utf-8");
    const [, pass] = decoded.split(":");
    if (pass === PASSWORD) return NextResponse.next();
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="RevWin Market Analysis"',
    },
  });
}

export const config = {
  // Public routes (skipped by basic auth):
  //   /business-case        — embedded as iframe on revwin.ai
  //   /api/business-case    — POST endpoint the iframe calls
  //   /api/jobs/<id>        — status polling for the iframe's running job
  //   _next/static, _next/image, favicon.ico — static assets
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|business-case|api/business-case|api/jobs).*)",
  ],
};
