import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Unit tests never load local .env files.
  envDir: false,
  resolve: {
    alias: { "@": path.resolve(__dirname) },
  },
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
    setupFiles: ["./tests/offline-setup.ts"],
    // Applied before test-module imports, including the real SDK's platform detection.
    env: {
      SNOWFLAKE_ACCOUNT: "offline",
      SNOWFLAKE_USER: "offline",
      SNOWFLAKE_PASSWORD: "not-a-credential",
      SNOWFLAKE_CONNECTION_NAME: "e2e-no-such-connection",
      SNOWFLAKE_DEFAULT_CONNECTION_NAME: "e2e-no-such-connection",
      SNOWFLAKE_DISABLE_PLATFORM_DETECTION: "true",
      SNOWFLAKE_DATABASE: "",
      SNOWFLAKE_SCHEMA: "",
      SNOWFLAKE_WAREHOUSE: "",
    },
  },
});
