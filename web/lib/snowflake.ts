// Snowflake access for the web dashboard.
//
// Inside the Application Service the platform injects a session token at
// /snowflake/session/token plus SNOWFLAKE_ACCOUNT, SNOWFLAKE_HOST and
// SNOWFLAKE_INSECURE_MODE (Phase 0 findings). Locally the helper falls back to
// SNOWFLAKE_USER / SNOWFLAKE_PASSWORD or the CLI `connections.toml` entry named by
// SNOWFLAKE_CONNECTION_NAME (default "default").
//
// Four connections are kept per process. Page dependencies load concurrently,
// while the bounded pool caps warehouse fan-out and reuses authenticated sessions.
// Queries run under owner's rights (decision D2); the viewer's identity is read
// from the ingress header in lib/identity.ts and never changes the session.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import snowflake from "snowflake-sdk";

const TOKEN_PATH = "/snowflake/session/token";

export type Row = Record<string, unknown>;

export interface ColumnMeta {
  readonly name: string;
  readonly type: string;
  readonly scale: number;
}

function serviceToken(): string | null {
  try {
    return fs.readFileSync(TOKEN_PATH, "utf8").trim();
  } catch {
    return null;
  }
}

// Reads one connection block from the Snowflake CLI connections file without a
// TOML dependency: the file is flat `[name]` sections of `key = "value"` lines.
function cliConnection(name: string): Record<string, string> {
  const file = path.join(os.homedir(), ".snowflake", "connections.toml");
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return {};
  }
  const section = text.split(/^\[/m).find((block) => block.startsWith(`${name}]`));
  if (!section) return {};
  const values: Record<string, string> = {};
  for (const line of section.split("\n").slice(1)) {
    const match = /^\s*([A-Za-z_]+)\s*=\s*"?([^"\n]*)"?\s*$/.exec(line);
    if (match) values[match[1].toLowerCase()] = match[2];
  }
  return values;
}

function connectionOptions(): snowflake.ConnectionOptions {
  const token = serviceToken();
  if (token) {
    return {
      account: process.env.SNOWFLAKE_ACCOUNT ?? "",
      host: process.env.SNOWFLAKE_HOST,
      authenticator: "OAUTH",
      token,
      database: process.env.SNOWFLAKE_DATABASE,
      schema: process.env.SNOWFLAKE_SCHEMA,
      insecureConnect: process.env.SNOWFLAKE_INSECURE_MODE === "true",
    } as snowflake.ConnectionOptions;
  }
  if (process.env.SNOWFLAKE_USER && process.env.SNOWFLAKE_PASSWORD) {
    return {
      account: process.env.SNOWFLAKE_ACCOUNT ?? "",
      username: process.env.SNOWFLAKE_USER,
      password: process.env.SNOWFLAKE_PASSWORD,
      warehouse: process.env.SNOWFLAKE_WAREHOUSE,
      database: process.env.SNOWFLAKE_DATABASE,
      schema: process.env.SNOWFLAKE_SCHEMA,
    } as snowflake.ConnectionOptions;
  }
  const cli = cliConnection(process.env.SNOWFLAKE_CONNECTION_NAME ?? "default");
  if (!cli.account) {
    throw new Error(
      "No Snowflake credentials: no session token, no SNOWFLAKE_USER/PASSWORD, and no CLI connection.",
    );
  }
  return {
    account: cli.account,
    username: cli.user,
    password: cli.password,
    authenticator: cli.authenticator?.toUpperCase(),
    privateKeyPath: cli.private_key_file ?? cli.private_key_path,
    warehouse: cli.warehouse,
    database: cli.database,
    schema: cli.schema,
    role: cli.role,
  } as snowflake.ConnectionOptions;
}

const connectionPool = snowflake.createPool(
  {
    ...connectionOptions(),
    // SDK 3.x defaults SQL NULL to "NULL"; nullable rates must not become NaN on rebinding.
    representNullAsStringNull: false,
  },
  {
    min: 0,
    max: 4,
    testOnBorrow: true,
    acquireTimeoutMillis: 30_000,
  },
);

// NUMBER and date-like values arrive as strings (fetchAsString) so nothing is
// rounded on the way in. Numbers are converted back unless an integer would lose
// precision, in which case the string is kept; dates stay ISO strings.
export function coerceValue(value: unknown, column: ColumnMeta): unknown {
  if (typeof value !== "string") return value;
  if (column.type === "fixed" || column.type === "real") {
    if (value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return value;
    if (column.type === "fixed" && column.scale === 0 && !Number.isSafeInteger(parsed))
      return value;
    return parsed;
  }
  return value;
}

export interface ExecuteResult {
  readonly rows: Row[];
  readonly columns: ColumnMeta[];
}

// Executes one statement with qmark binds; column names are upper-cased.
export async function executeStatement(
  sql: string,
  params: readonly unknown[] = [],
): Promise<ExecuteResult> {
  return connectionPool.use(
    (connection) =>
      new Promise<ExecuteResult>((resolve, reject) =>
        connection.execute({
          sqlText: sql,
          binds: [...params] as snowflake.Binds,
          fetchAsString: ["Number", "Date"],
          complete: (err, stmt, rows) => {
            if (err) {
              reject(err);
              return;
            }
            const columns: ColumnMeta[] = (stmt.getColumns() ?? []).map((c) => ({
              name: c.getName().toUpperCase(),
              type: c.getType(),
              scale: c.getScale(),
            }));
            const byName = new Map(
              (stmt.getColumns() ?? []).map((c, i) => [c.getName(), columns[i]]),
            );
            const coerced = ((rows ?? []) as Row[]).map((row) => {
              const out: Row = {};
              for (const [name, value] of Object.entries(row)) {
                const column = byName.get(name) ?? {
                  name: name.toUpperCase(),
                  type: "",
                  scale: 0,
                };
                out[column.name] = coerceValue(value, column);
              }
              return out;
            });
            resolve({ rows: coerced, columns });
          },
        }),
      ),
  );
}
