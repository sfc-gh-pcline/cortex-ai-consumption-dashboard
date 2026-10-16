// Shared SQL building blocks. Port of the constants and helpers at the top of
// utils/queries.py and the identifier helpers in utils/snowflake.py. Every
// string here must stay byte-identical to the Python source: the parity test
// compares generated statements character for character.
import type { IsoDate } from "@/lib/view-state";
import { addDays } from "@/lib/dates";

export type Param = string | number | null;

export interface Statement {
  readonly sql: string;
  readonly params: readonly Param[];
}

export const AU = "SNOWFLAKE.ACCOUNT_USAGE";
export const COCO_USAGE_VIEW = "SNOWFLAKE_COCO_USAGE_HISTORY";
export const COCO_APP_NAMES = ["cortex_code_cli", "cortex_code_desktop", "cortex_code_sandbox"];
export const TIER_PER_REQUEST = "per_request";
export const TIER_SURFACE = "surface";
export const TOKEN_TYPES = ["input", "output", "cache_read_input", "cache_write_input"] as const;
export const TOKEN_TYPE_LABELS: Readonly<Record<string, string>> = {
  input: "Input",
  output: "Output",
  cache_read_input: "Cache read",
  cache_write_input: "Cache write",
};
export const TOKEN_TYPE_COLUMNS: Readonly<Record<string, string>> = Object.fromEntries(
  TOKEN_TYPES.map((t) => [t, `TOK_${t.toUpperCase()}`]),
);
export const RATE_COLUMNS = ["R_INPUT", "R_OUTPUT", "R_CACHE_READ", "R_CACHE_WRITE"] as const;
export const UNKNOWN = "(unknown)";
export const NONE_LABEL = "(none)";
export const RATES_WINDOW_DAYS = 90;

// SQL expression for the UTC calendar day of a timestamp column.
export function utcDay(column: string): string {
  return `CONVERT_TIMEZONE('UTC', ${column})::DATE`;
}

// Half-open UTC window predicate with two `?` placeholders (see windowParams).
export function utcWindow(column: string): string {
  return `${column} >= ?::TIMESTAMP_TZ AND ${column} < ?::TIMESTAMP_TZ`;
}

// Midnight UTC on start, then midnight UTC on the day after end, as TIMESTAMP_TZ literals.
export function windowParams(start: IsoDate, end: IsoDate): string[] {
  return [`${start} 00:00:00 +0000`, `${addDays(end, 1)} 00:00:00 +0000`];
}

const IDENT_RE = /^[A-Za-z_][A-Za-z0-9_$]*$/;
const MAX_IDENT_LEN = 255;

// Python-style repr for the error messages the parity fixture pins.
const repr = (value: string) => `'${value}'`;

// Validates and double-quotes a single-part Snowflake identifier in uppercase.
export function quoteIdentifier(name: string): string {
  if (!name || name.length > MAX_IDENT_LEN) {
    throw new Error(
      `Identifier must be 1-${MAX_IDENT_LEN} characters, got ${name ? name.length : 0}.`,
    );
  }
  if (!IDENT_RE.test(name)) {
    throw new Error(
      `Identifier ${repr(name)} contains invalid characters. ` +
        "Only letters, digits, underscores, and dollar signs are allowed " +
        "(must start with a letter or underscore).",
    );
  }
  for (const bad of [";", "--", "/*", "*/"]) {
    if (name.includes(bad)) {
      throw new Error(`Identifier ${repr(name)} contains forbidden token ${repr(bad)}.`);
    }
  }
  if (name.includes(".")) {
    throw new Error(`Identifier ${repr(name)} contains a dot — use single-part names only.`);
  }
  if (/\s/.test(name)) {
    throw new Error(`Identifier ${repr(name)} contains whitespace.`);
  }
  return `"${name.toUpperCase()}"`;
}

// Converts a surface key to a table/task name suffix ("CoWork — Service Mix" -> "COWORK_SERVICE_MIX").
export function surfaceToSuffix(surfaceKey: string): string {
  return surfaceKey.toUpperCase().replaceAll(" — ", "_").replaceAll("—", "_").replaceAll(" ", "_");
}

// Fully qualified, quoted DB.SCHEMA.NAME.
export function fqn(database: string, schema: string, name: string): string {
  return `${quoteIdentifier(database)}.${quoteIdentifier(schema)}.${quoteIdentifier(name)}`;
}

// Formats a number the way Python's str(float) does, so inlined rate literals on
// the Reference SQL page read identically in both apps: shortest round-trip
// digits, scientific notation when the exponent is below -4 or at least 16, and
// a two-digit signed exponent ("3e-06", "1e+16", "0.0001", "3.0").
export function pythonFloatRepr(value: number): string {
  if (Number.isNaN(value)) return "nan";
  if (!Number.isFinite(value)) return value > 0 ? "inf" : "-inf";
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";
  const sign = value < 0 ? "-" : "";
  const [mantissa, exponentText] = Math.abs(value).toExponential().split("e");
  const exponent = Number(exponentText);
  const digits = mantissa.replace(".", "");
  if (exponent < -4 || exponent >= 16) {
    const exp = `${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(2, "0")}`;
    return `${sign}${mantissa}e${exp}`;
  }
  if (exponent < 0) {
    return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`;
  }
  const intPart = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0");
  const fracPart = digits.slice(exponent + 1) || "0";
  return `${sign}${intPart}.${fracPart}`;
}

// Substitutes bound qmark parameters into SQL for display, quoting each value.
export function inlineParams(sql: string, params: readonly Param[]): string {
  let index = 0;
  let out = "";
  for (const char of sql) {
    if (char === "?" && index < params.length) {
      const value = params[index++];
      if (value === null) {
        out += "NULL";
        continue;
      }
      const text = typeof value === "number" ? pythonFloatRepr(value) : value;
      out += `'${text.replaceAll("'", "''")}'`;
    } else {
      out += char;
    }
  }
  return out.trim();
}
