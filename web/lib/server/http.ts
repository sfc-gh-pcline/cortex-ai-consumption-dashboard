// Small helpers shared by the route handlers.
import { z } from "zod";

import { isIsoDate } from "@/lib/dates";
import { ForbiddenError } from "@/lib/identity";

export const isoDate = z.string().refine(isIsoDate, "Expected a calendar date (YYYY-MM-DD)");

export const windowQuery = z
  .object({ start: isoDate, end: isoDate })
  .refine((w) => w.start <= w.end, "Window end must be on or after its start.");

export function searchParams(request: Request): Record<string, string> {
  return Object.fromEntries(new URL(request.url).searchParams.entries());
}

export function flag(params: Record<string, string>, name: string): boolean {
  return params[name] === "1" || params[name] === "true";
}

export function jsonError(status: number, message: string, detail?: unknown): Response {
  return Response.json({ error: message, detail }, { status });
}

// Maps thrown errors to HTTP responses: validation 400, authorization 403, else 500.
export function errorResponse(err: unknown): Response {
  if (err instanceof z.ZodError) return jsonError(400, "Invalid request", err.issues);
  if (err instanceof ForbiddenError) return jsonError(403, err.message);
  const requestId = crypto.randomUUID();
  console.error(`[${requestId}] dashboard request failed`, err);
  return Response.json({ error: "The dashboard request failed.", requestId }, { status: 500 });
}
