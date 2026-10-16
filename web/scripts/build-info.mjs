// Build identity captured at `next build` time and inlined into the bundle as
// NEXT_PUBLIC_BUILD_INFO (see next.config.ts). Mirrors utils/runtime.py:
// the declared project version, a SHA-256 digest over the runtime source files
// (so two builds of the same source share a hash), the git commit when a
// checkout is available (the App Runtime builder receives source without .git),
// and the versions of the major components the page depends on.
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const RUNTIME_DIRS = ["app", "components", "lib"];
const RUNTIME_FILES = ["package.json", "app.yml", "next.config.ts"];
const COMPONENTS = [
  ["Next.js", "next"],
  ["React", "react"],
  ["Tailwind CSS", "tailwindcss"],
  ["TanStack Table", "@tanstack/react-table"],
  ["TanStack Query", "@tanstack/react-query"],
  ["react-vega", "react-vega"],
  ["Vega-Lite", "vega-lite"],
  ["nuqs", "nuqs"],
  ["snowflake-sdk", "snowflake-sdk"],
];

function* walk(dir) {
  for (const entry of readdirSync(dir).sort()) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* walk(path);
    else yield path;
  }
}

// Sorted runtime source paths relative to root.
export function runtimeFiles(root) {
  const files = [];
  for (const dir of RUNTIME_DIRS) {
    try {
      for (const path of walk(join(root, dir))) files.push(relative(root, path));
    } catch {
      // directory absent in a partial checkout
    }
  }
  for (const file of RUNTIME_FILES) {
    try {
      statSync(join(root, file));
      files.push(file);
    } catch {
      // optional
    }
  }
  return files.sort();
}

// First 12 hex characters of SHA-256 over each path and its bytes, like utils/runtime.py.
export function artifactDigest(root, files = runtimeFiles(root)) {
  const digest = createHash("sha256");
  for (const file of files) {
    digest.update(file.split("\\").join("/"));
    digest.update(readFileSync(join(root, file)));
  }
  return digest.digest("hex").slice(0, 12);
}

function packageVersion(root, name) {
  try {
    return JSON.parse(readFileSync(join(root, "node_modules", name, "package.json"), "utf8"))
      .version;
  } catch {
    return "unknown";
  }
}

function gitSha(root) {
  try {
    return execSync("git rev-parse --short=12 HEAD", {
      cwd: root,
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
  } catch {
    return "";
  }
}

export function buildInfo(root = process.cwd()) {
  const files = runtimeFiles(root);
  return {
    projectVersion:
      JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version ?? "unknown",
    artifactDigest: artifactDigest(root, files),
    runtimeFiles: files.length,
    gitSha: gitSha(root),
    builtAt: new Date().toISOString(),
    node: process.version,
    components: COMPONENTS.map(([label, name]) => ({ label, version: packageVersion(root, name) })),
  };
}
