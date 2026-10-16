import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildCaption, fingerprintCaption, parseBuildInfo } from "@/lib/build-info";
import { artifactDigest, buildInfo, runtimeFiles } from "../scripts/build-info.mjs";

function project(): string {
  const root = mkdtempSync(join(tmpdir(), "build-info-"));
  mkdirSync(join(root, "lib"));
  mkdirSync(join(root, "app", "x"), { recursive: true });
  writeFileSync(join(root, "package.json"), JSON.stringify({ version: "1.2.3" }));
  writeFileSync(join(root, "lib", "a.ts"), "export const a = 1;\n");
  writeFileSync(join(root, "app", "x", "page.tsx"), "export default () => null;\n");
  return root;
}

describe("build info", () => {
  it("digests sorted runtime files deterministically and changes with content", () => {
    const root = project();
    expect(runtimeFiles(root)).toEqual(["app/x/page.tsx", "lib/a.ts", "package.json"]);
    const first = artifactDigest(root);
    expect(first).toMatch(/^[0-9a-f]{12}$/);
    expect(artifactDigest(root)).toBe(first);
    writeFileSync(join(root, "lib", "a.ts"), "export const a = 2;\n");
    expect(artifactDigest(root)).not.toBe(first);
  });

  it("reports the project version, file count and unknown component versions", () => {
    const info = buildInfo(project());
    expect(info.projectVersion).toBe("1.2.3");
    expect(info.runtimeFiles).toBe(3);
    expect(info.components.find((c) => c.label === "React")?.version).toBe("unknown");
    expect(info.node).toBe(process.version);
  });

  it("degrades to dev when the inlined value is missing or malformed", () => {
    expect(parseBuildInfo(undefined).projectVersion).toBe("dev");
    expect(parseBuildInfo("{not json").artifactDigest).toBe("unknown");
    expect(buildCaption(parseBuildInfo(undefined))).toBe("Next.js dev | build unknown");
    const info = parseBuildInfo(
      JSON.stringify({
        projectVersion: "0.1.0",
        artifactDigest: "abcdef123456",
        runtimeFiles: 90,
        gitSha: "53a4d0d1e2f3",
        builtAt: "2026-09-24T00:00:00.000Z",
        components: [{ label: "Next.js", version: "16.3.6" }],
      }),
    );
    expect(buildCaption(info)).toBe("Next.js 16.3.6 | build abcdef123456");
    expect(fingerprintCaption(info)).toBe(
      "Build 0.1.0 / abcdef123456 (90 runtime files) | commit 53a4d0d1e2f3 | built 2026-09-24 00:00 UTC",
    );
  });
});
