// Completes the standalone bundle: Next.js writes .next/standalone/server.js but
// leaves .next/static and public/ for the deployer to copy (see the Next docs on
// output: "standalone"). App Runtime runs `node .next/standalone/server.js`, so
// without this step the served pages would have no CSS or client JavaScript.
import { cpSync, existsSync } from "node:fs";

const standalone = ".next/standalone";
if (!existsSync(`${standalone}/server.js`)) {
  console.log("postbuild: no standalone output, nothing to copy");
  process.exit(0);
}
cpSync(".next/static", `${standalone}/.next/static`, { recursive: true, force: true });
if (existsSync("public"))
  cpSync("public", `${standalone}/public`, { recursive: true, force: true });
console.log("postbuild: copied .next/static and public into .next/standalone");
