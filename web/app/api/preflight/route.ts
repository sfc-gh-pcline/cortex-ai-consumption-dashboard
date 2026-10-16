// Connection, warehouse, and ACCOUNT_USAGE checks plus the viewer's identity.
import { allowedAdmins, isAdmin, viewerFromHeaders } from "@/lib/identity";
import { errorResponse } from "@/lib/server/http";
import { runPreflight, utcToday } from "@/lib/server/preflight";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const viewer = viewerFromHeaders(request.headers);
    const preflight = await runPreflight();
    const today = preflight.checks.some((c) => c.level === "error") ? null : await utcToday();
    return Response.json({
      preflight,
      today,
      viewer,
      isAdmin: isAdmin(viewer, allowedAdmins()),
    });
  } catch (err) {
    return errorResponse(err);
  }
}
