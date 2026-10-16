import { defineConfig, devices } from "@playwright/test";

// Smoke suite with a mocked /api layer (see e2e/mocks.ts), so it runs without a
// Snowflake connection. `make web:e2e` selects the server and loopback port:
// E2E_SERVER=dev (next dev) or production (the built standalone server), E2E_PORT.
const port = Number(process.env.E2E_PORT ?? "3100");
const production = process.env.E2E_SERVER === "production";
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: origin,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: production
      ? "node .next/standalone/server.js"
      : `npm run dev -- --hostname 127.0.0.1 --port ${port}`,
    // A static page, so readiness polling never reaches the Snowflake-backed routes.
    url: `${origin}/sql`,
    // Never attach to a server this run did not start; it could be a live preview.
    reuseExistingServer: false,
    timeout: 120_000,
    // Playwright merges these over the caller's environment. Blank the credential
    // variables and point at a connection that does not exist, so an un-mocked /api
    // call fails fast instead of querying a real account.
    env: {
      HOSTNAME: "127.0.0.1",
      PORT: String(port),
      SNOWFLAKE_ACCOUNT: "",
      SNOWFLAKE_HOST: "",
      SNOWFLAKE_USER: "",
      SNOWFLAKE_PASSWORD: "",
      SNOWFLAKE_CONNECTION_NAME: "e2e-no-such-connection",
      SNOWFLAKE_DEFAULT_CONNECTION_NAME: "e2e-no-such-connection",
      SNOWFLAKE_DISABLE_PLATFORM_DETECTION: "true",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});
