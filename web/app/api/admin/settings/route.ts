// Persists acceleration settings: either the enabled flag or the Setup form.
import { z } from "zod";

import { applySetup, WAREHOUSE_SIZES } from "@/lib/admin/actions";
import { requireAdmin } from "@/lib/identity";
import { saveSettings } from "@/lib/server/admin";
import { requestContext } from "@/lib/server/context";
import { errorResponse } from "@/lib/server/http";

export const dynamic = "force-dynamic";

const body = z.union([
  z.object({ enabled: z.boolean() }),
  z.object({
    database: z.string(),
    schema: z.string(),
    warehouseName: z.string(),
    warehouseSize: z.enum(WAREHOUSE_SIZES),
    objectPrefix: z.string(),
  }),
]);

export async function POST(request: Request) {
  try {
    requireAdmin(request.headers);
    const input = body.parse(await request.json());
    const ctx = await requestContext({ reload: true });
    const candidate =
      "enabled" in input
        ? { ...ctx.settings, enabled: input.enabled }
        : applySetup(ctx.settings, input);
    await saveSettings(candidate);
    return Response.json({ settings: candidate });
  } catch (err) {
    return errorResponse(err);
  }
}
