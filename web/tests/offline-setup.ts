import dns from "node:dns";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import { syncBuiltinESMExports } from "node:module";
import net from "node:net";
import tls from "node:tls";
import { afterAll, expect } from "vitest";

// The production transport prefers this file over environment options. Never read it.
if (fs.existsSync("/snowflake/session/token")) {
  throw new Error("Offline tests cannot run with a hosted Snowflake token");
}

let networkAttempts = 0;
function denyNetwork(): never {
  networkAttempts += 1;
  throw new Error("Offline test attempted network access; mock the transport instead");
}

function block<Target extends object, Key extends keyof Target>(target: Target, key: Key) {
  Object.assign(target, { [key]: denyNetwork });
}

// Keep the SDK and pool real. Existing transport tests can still supply their own mocks.
block(net.Socket.prototype, "connect");
block(net, "connect");
block(net, "createConnection");
block(tls, "connect");
block(http, "request");
block(http, "get");
block(https, "request");
block(https, "get");
block(dns, "lookup");
block(dns, "resolve");
block(globalThis, "fetch");
syncBuiltinESMExports();

afterAll(() => {
  // Leave guards installed: SDK retries must stay blocked after a test ends.
  // SDK detection and application fallbacks can catch errors. Still fail the suite.
  expect(networkAttempts, "Unexpected network attempts in an offline test").toBe(0);
});
