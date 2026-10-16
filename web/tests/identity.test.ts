// Fail-closed admin gate: mirrors tests/test_admin_actions.py authorization cases.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { allowedAdmins, isAdmin, parseAllowlist, viewerFromHeaders } from "@/lib/identity";

describe("viewer identity", () => {
  it("reads the ingress header and rejects blanks", () => {
    expect(viewerFromHeaders(new Headers({ "sf-context-current-user": "MYOUNG" }))).toBe("MYOUNG");
    expect(viewerFromHeaders(new Headers({ "sf-context-current-user": "  " }))).toBeNull();
    expect(viewerFromHeaders(new Headers())).toBeNull();
  });
});

describe("allowlist", () => {
  it("accepts only a JSON array of non-empty strings", () => {
    expect(parseAllowlist('["A", "B"]')).toEqual(["A", "B"]);
    expect(parseAllowlist('["A", ""]')).toEqual([]);
    expect(parseAllowlist('"A"')).toEqual([]);
    expect(parseAllowlist("not json")).toEqual([]);
    expect(parseAllowlist(undefined)).toEqual([]);
  });

  it("prefers the environment and falls back to the file", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "admin-"));
    const file = path.join(dir, "admin_access.json");
    fs.writeFileSync(file, JSON.stringify({ users: ["FILE_ADMIN"] }));
    expect(allowedAdmins({ DASHBOARD_ADMIN_USERS: '["ENV_ADMIN"]' }, file)).toEqual(["ENV_ADMIN"]);
    expect(allowedAdmins({}, file)).toEqual(["FILE_ADMIN"]);
    expect(allowedAdmins({}, path.join(dir, "missing.json"))).toEqual([]);
    expect(allowedAdmins({ DASHBOARD_ADMIN_USERS: "[]" }, file)).toEqual([]);
  });

  it("authorizes only an exact allowlisted viewer", () => {
    expect(isAdmin("MYOUNG", ["MYOUNG"])).toBe(true);
    expect(isAdmin("myoung", ["MYOUNG"])).toBe(false);
    expect(isAdmin(null, ["MYOUNG"])).toBe(false);
    expect(isAdmin("MYOUNG", [])).toBe(false);
  });
});
