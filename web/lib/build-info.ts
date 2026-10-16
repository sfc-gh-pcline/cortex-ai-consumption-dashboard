// Build identity inlined at `next build` by next.config.ts (scripts/build-info.mjs).
// In `next dev` and tests the variable is still set by the config, but a missing
// or malformed value degrades to "dev" rather than breaking the shell.
export interface BuildInfo {
  readonly projectVersion: string;
  readonly artifactDigest: string;
  readonly runtimeFiles: number;
  readonly gitSha: string;
  readonly builtAt: string;
  readonly node: string;
  readonly components: readonly { label: string; version: string }[];
}

const FALLBACK: BuildInfo = {
  projectVersion: "dev",
  artifactDigest: "unknown",
  runtimeFiles: 0,
  gitSha: "",
  builtAt: "",
  node: "",
  components: [],
};

export function parseBuildInfo(raw: string | undefined): BuildInfo {
  if (!raw) return FALLBACK;
  try {
    const parsed = JSON.parse(raw) as Partial<BuildInfo>;
    return { ...FALLBACK, ...parsed, components: parsed.components ?? [] };
  } catch {
    return FALLBACK;
  }
}

export const BUILD_INFO: BuildInfo = parseBuildInfo(process.env.NEXT_PUBLIC_BUILD_INFO);

// Sidebar caption, the counterpart of "Streamlit <v> | build <digest>".
export function buildCaption(info: BuildInfo = BUILD_INFO): string {
  const next = info.components.find((c) => c.label === "Next.js")?.version ?? "dev";
  return `Next.js ${next} | build ${info.artifactDigest}`;
}

// Config caption, the counterpart of render_fingerprint's first line.
export function fingerprintCaption(info: BuildInfo = BUILD_INFO): string {
  const git = info.gitSha ? ` | commit ${info.gitSha}` : "";
  const built = info.builtAt ? ` | built ${info.builtAt.slice(0, 16).replace("T", " ")} UTC` : "";
  return `Build ${info.projectVersion} / ${info.artifactDigest} (${info.runtimeFiles} runtime files)${git}${built}`;
}
