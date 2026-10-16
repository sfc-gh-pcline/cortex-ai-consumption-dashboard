// Acceleration settings, aggregate health, and source routing for every dependency.
import {
  depSourceMode,
  hasHealthySurfaces,
  latestSnapshotFreshness,
  readiness,
} from "@/lib/health";
import { DEPENDENCY_KINDS } from "@/lib/plans";
import { requireAdmin } from "@/lib/identity";
import { generations, requestContext } from "@/lib/server/context";
import { errorResponse, flag, searchParams } from "@/lib/server/http";
import { configLocation } from "@/lib/server/settings";
import { sourceQueries } from "@/lib/server/query";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const params = searchParams(request);
    const reload = flag(params, "reload");
    if (reload) requireAdmin(request.headers);
    const ctx = await requestContext({ reload });
    return Response.json({
      settings: ctx.settings,
      location: configLocation(),
      today: ctx.today,
      statuses: ctx.statuses,
      readiness: Object.fromEntries(
        Object.entries(ctx.statuses).map(([key, status]) => [key, readiness(status)]),
      ),
      freshness: latestSnapshotFreshness(ctx.statuses),
      healthy: hasHealthySurfaces(ctx.statuses),
      sourceModes: Object.fromEntries(
        Object.keys(DEPENDENCY_KINDS).map((dep) => [
          dep,
          depSourceMode(dep, ctx.settings, ctx.statuses),
        ]),
      ),
      generations: generations(ctx),
      sourceQueryCount: sourceQueries(),
    });
  } catch (err) {
    return errorResponse(err);
  }
}
