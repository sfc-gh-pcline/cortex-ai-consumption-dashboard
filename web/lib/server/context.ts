// Assembles the per-request loader context: settings, aggregate health, and
// Snowflake's UTC today, all served from their own caches.
import { dependencyGeneration, type Statuses } from "@/lib/health";
import { DEPENDENCY_KINDS } from "@/lib/plans";
import { invalidateHealth, loadStatuses } from "@/lib/server/health";
import type { LoaderContext } from "@/lib/server/loaders";
import { utcToday } from "@/lib/server/preflight";
import { runQuery } from "@/lib/server/query";
import { invalidateSettings, loadSettings } from "@/lib/server/settings";
import type { Settings } from "@/lib/settings";

export interface RequestContext extends LoaderContext {
  readonly settings: Settings;
  readonly statuses: Statuses;
}

export async function requestContext(
  options: { reload?: boolean; refresh?: boolean } = {},
): Promise<RequestContext> {
  if (options.reload) {
    invalidateSettings();
    invalidateHealth();
  }
  const settings = await loadSettings();
  const [statuses, today] = await Promise.all([loadStatuses(settings), utcToday()]);
  return {
    settings,
    statuses,
    today,
    run: runQuery,
    bypassCache: options.reload || options.refresh,
  };
}

// Current generation for every registered dependency, for client cache keys.
export function generations(ctx: RequestContext): Record<string, string> {
  return Object.fromEntries(
    Object.keys(DEPENDENCY_KINDS).map((dep) => [
      dep,
      dependencyGeneration(dep, ctx.settings, ctx.statuses),
    ]),
  );
}
