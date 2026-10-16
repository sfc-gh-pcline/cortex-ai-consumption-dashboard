// Admin-only view of the acceleration layer: untracked objects, eligible
// lifecycle targets, and the viewer's last provision and lifecycle reports.
import { requireAdmin } from "@/lib/identity";
import {
  eligibleTargets,
  lastLifecycleReport,
  lastProvisionReport,
  untrackedObjects,
} from "@/lib/server/admin";
import { requestContext } from "@/lib/server/context";
import { errorResponse } from "@/lib/server/http";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const viewer = requireAdmin(request.headers);
    const ctx = await requestContext();
    return Response.json({
      untracked: await untrackedObjects(ctx.settings),
      eligible: eligibleTargets(ctx.settings, ctx.statuses),
      provisionReport: lastProvisionReport(viewer),
      lifecycleReport: lastLifecycleReport(viewer),
    });
  } catch (err) {
    return errorResponse(err);
  }
}
