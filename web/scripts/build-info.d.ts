export interface BuildInfo {
  projectVersion: string;
  artifactDigest: string;
  runtimeFiles: number;
  gitSha: string;
  builtAt: string;
  node: string;
  components: { label: string; version: string }[];
}
export function runtimeFiles(root: string): string[];
export function artifactDigest(root: string, files?: string[]): string;
export function buildInfo(root?: string): BuildInfo;
