// Creates the task warehouse, aggregate tables and refresh tasks, or retries the
// viewer's last failed provisioning report without replaying successful steps.
import { z } from "zod";

import { requireAdmin } from "@/lib/identity";
import { provision } from "@/lib/server/admin";
import { requestContext } from "@/lib/server/context";
import { errorResponse } from "@/lib/server/http";

export const dynamic = "force-dynamic";

const body = z.object({ retry: z.boolean().default(false) });

export async function POST(request: Request) {
  try {
    const viewer = requireAdmin(request.headers);
    const { retry } = body.parse(await request.json().catch(() => ({})));
    const ctx = await requestContext({ reload: true });
    const report = await provision(viewer, ctx.settings, retry);
    return Response.json({ report });
  } catch (err) {
    return errorResponse(err);
  }
}
