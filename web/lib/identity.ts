// Viewer identity and fail-closed admin authorization. Port of utils/admin.py.
//
// The Application Service ingress sets `sf-context-current-user` on every
// request (Phase 0 finding). Authorization matches that name against an
// owner-managed allowlist: the DASHBOARD_ADMIN_USERS environment variable (a
// JSON array set in app.yml) or, when that is unset, admin_access.json in the
// project root. The client never decides; route handlers call requireAdmin.
import fs from "node:fs";
import path from "node:path";

export const IDENTITY_HEADER = "sf-context-current-user";

export function viewerFromHeaders(headers: Headers): string | null {
  const value = headers.get(IDENTITY_HEADER)?.trim();
  return value ? value : null;
}

// Parses an allowlist value; anything but a JSON array of non-empty strings is empty.
export function parseAllowlist(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed) || !parsed.every((n) => typeof n === "string" && n.length > 0)) {
      return [];
    }
    return parsed;
  } catch {
    return [];
  }
}

// Reads the allowlist from the environment, then the checked-in file.
export function allowedAdmins(
  env: Readonly<Record<string, string | undefined>> = process.env,
  filePath: string = path.join(process.cwd(), "admin_access.json"),
): string[] {
  if (env.DASHBOARD_ADMIN_USERS !== undefined) return parseAllowlist(env.DASHBOARD_ADMIN_USERS);
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const users = (parsed as { users?: unknown })?.users;
    return parseAllowlist(JSON.stringify(users ?? null));
  } catch {
    return [];
  }
}

export function isAdmin(viewer: string | null, allowed: readonly string[]): boolean {
  return viewer !== null && viewer.length > 0 && allowed.includes(viewer);
}

export class ForbiddenError extends Error {
  constructor() {
    super("This action requires an authorized dashboard administrator.");
    this.name = "ForbiddenError";
  }
}

// Throws unless the request's viewer is an allowlisted administrator.
export function requireAdmin(headers: Headers): string {
  const viewer = viewerFromHeaders(headers);
  if (!isAdmin(viewer, allowedAdmins())) throw new ForbiddenError();
  return viewer as string;
}
