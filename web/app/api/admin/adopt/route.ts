// Registers configured objects that exist but are unowned, so objects created by
// sql/config.sql become eligible for lifecycle actions and teardown.
import { requireAdmin } from "@/lib/identity";
import { adopt } from "@/lib/server/admin";
import { requestContext } from "@/lib/server/context";
import { errorResponse } from "@/lib/server/http";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    requireAdmin(request.headers);
    const ctx = await requestContext({ reload: true });
    return Response.json({ adopted: await adopt(ctx.settings) });
  } catch (err) {
    return errorResponse(err);
  }
}
