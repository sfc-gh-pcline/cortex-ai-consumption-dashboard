import type { NextConfig } from "next";

import { buildInfo } from "./scripts/build-info.mjs";

const nextConfig: NextConfig = {
  // App Runtime sets NEXT_PRIVATE_STANDALONE and expects the standalone server;
  // app.yml runs `node .next/standalone/server.js` to match (Phase 0 finding).
  output: "standalone",
  // Build identity (version, source digest, git commit, component versions) is
  // computed once here and inlined, so every page shows what it was built from.
  env: { NEXT_PUBLIC_BUILD_INFO: JSON.stringify(buildInfo()) },
};

export default nextConfig;
