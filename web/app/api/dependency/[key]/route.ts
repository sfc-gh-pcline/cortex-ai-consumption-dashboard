// One declared dependency for one window. The client issues one request per
// dependency, so the loading panel can report each outcome as it lands.
import { dependencyGeneration } from "@/lib/health";
import { requireAdmin } from "@/lib/identity";
import { DEPENDENCY_KINDS } from "@/lib/plans";
import { requestContext } from "@/lib/server/context";
import { errorResponse, flag, jsonError, searchParams, windowQuery } from "@/lib/server/http";
import { loadDependency } from "@/lib/server/loaders";
import { validateRequestWindow } from "@/lib/dates";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ key: string }> }) {
  try {
    const { key } = await context.params;
    const dependency = decodeURIComponent(key);
    if (!(dependency in DEPENDENCY_KINDS))
      return jsonError(404, `Unknown dependency: ${dependency}`);
    const params = searchParams(request);
    const requested = windowQuery.parse(params);
    const reload = flag(params, "reload");
    const refresh = flag(params, "refresh");
    if (reload || refresh) requireAdmin(request.headers);
    const base = await requestContext({ reload, refresh });
    const window = validateRequestWindow(base.today, requested);
    // `direct=1` bypasses aggregate routing for an explicitly requested partial-day
    // view ("Today so far"), like load_direct_dependency in the Streamlit app.
    const ctx = flag(params, "direct")
      ? { ...base, settings: { ...base.settings, enabled: false } }
      : base;
    const result = await loadDependency(dependency, window, ctx);
    return Response.json({
      ...result,
      generation: dependencyGeneration(dependency, ctx.settings, ctx.statuses),
      window,
    });
  } catch (err) {
    return errorResponse(err);
  }
}
