// Task lifecycle actions (suspend, resume, refresh) and teardown (drop) over
// exact registered targets only.
import { z } from "zod";

import { LIFECYCLE_ACTIONS } from "@/lib/admin/actions";
import { requireAdmin } from "@/lib/identity";
import { lifecycle } from "@/lib/server/admin";
import { requestContext } from "@/lib/server/context";
import { errorResponse } from "@/lib/server/http";

export const dynamic = "force-dynamic";

const body = z.object({
  action: z.enum(LIFECYCLE_ACTIONS),
  targets: z.array(z.string().min(1)),
  retry: z.boolean().default(false),
});

export async function POST(request: Request) {
  try {
    const viewer = requireAdmin(request.headers);
    const input = body.parse(await request.json());
    const ctx = await requestContext({ reload: true });
    const report = await lifecycle(viewer, input.action, input.targets, ctx.settings, input.retry);
    return Response.json({ report });
  } catch (err) {
    return errorResponse(err);
  }
}
