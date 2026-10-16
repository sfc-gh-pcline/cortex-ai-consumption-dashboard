// Preflight for `make web:e2e`: fail before Playwright starts a server when inputs are
// invalid, a hosted session token is present, the loopback port is already in use (an
// unrelated or live server would otherwise answer the tests), or a production run has
// no standalone build. Reads E2E_SERVER (dev | production) and E2E_PORT (1024-65535).
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const server = process.env.E2E_SERVER ?? "dev";
const portText = process.env.E2E_PORT ?? "3100";

function fail(message) {
  console.error(`e2e preflight: ${message}`);
  process.exit(2);
}

if (server !== "dev" && server !== "production") fail("E2E_SERVER must be dev or production.");
if (!/^\d{4,5}$/.test(portText) || Number(portText) < 1024 || Number(portText) > 65535)
  fail("E2E_PORT must be an integer from 1024 to 65535.");
if (existsSync("/snowflake/session/token"))
  fail("/snowflake/session/token exists; mocked browser tests must not run in a hosted container.");
if (server === "production" && !existsSync(join(web, ".next/standalone/server.js")))
  fail("no production build; run `make web:build` first.");

const probe = createServer();
probe.once("error", () =>
  fail(`127.0.0.1:${portText} is in use; stop that server or set E2E_PORT.`),
);
probe.listen(Number(portText), "127.0.0.1", () => probe.close());
