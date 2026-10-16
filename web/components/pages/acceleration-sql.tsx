"use client";

import { CodeBlock } from "@/components/kit/code-block";
import { Expander, PageHeader, Section } from "@/components/kit/layout";
import { Caption, Notice } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { Badge } from "@/components/ui/badge";
import { useDashboard } from "@/lib/client/dashboard";
import { useDependencies } from "@/lib/client/queries";
import { useStringParam } from "@/lib/client/url-state";
import { PAGE_QUERY_ACCELERATION_SQL, SURFACE_MODEL_RATES } from "@/lib/plans";
import { rowsAsRates } from "@/lib/rates";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { DT_BODY_REGISTRY, accelerationLifecycleSql } from "@/lib/sql/acceleration";
import { inlineParams, surfaceToSuffix } from "@/lib/sql/core";
import {
  ACCELERATION_SQL_REFERENCE,
  SQL_REFERENCE,
  referenceStatementFor,
} from "@/lib/sql/reference";

export function AccelerationSqlPage() {
  const { displayWindow, today, loadWindow, settings: response } = useDashboard();
  const settings = response?.settings ?? DEFAULT_SETTINGS;
  const keys = ACCELERATION_SQL_REFERENCE.map((e) => e.surfaceKey);
  const [surfaceParam, setSurface] = useStringParam("accel_surface", keys[0]);
  const surfaceKey = keys.includes(surfaceParam) ? surfaceParam : keys[0];
  const entry = ACCELERATION_SQL_REFERENCE.find((e) => e.surfaceKey === surfaceKey)!;
  const needsRates = surfaceKey === "Cortex Inference API";
  const [rates] = useDependencies([SURFACE_MODEL_RATES], loadWindow, response, needsRates);
  const sourceFor = (key: string) =>
    (response?.sourceModes[key]?.[0] === "aggregate" ? "dt" : "account_usage") as
      "dt" | "account_usage";
  const reference = SQL_REFERENCE.find((e) => e.runtimeKey === surfaceKey);
  const statement =
    reference && displayWindow && today
      ? referenceStatementFor(reference, displayWindow, settings, {
          today,
          rates: rates.data ? rowsAsRates(rates.data.rows) : [],
          sourceFor,
        })
      : null;
  const isDt = sourceFor(surfaceKey) === "dt";
  const lifecycle = accelerationLifecycleSql(surfaceKey, settings);
  const body = DT_BODY_REGISTRY[surfaceKey].body();
  const suffix = surfaceToSuffix(surfaceKey);
  const tableFqn = `"${settings.database}"."${settings.schema}"."${settings.objectPrefix}_${suffix}"`;
  const taskName = `${settings.objectPrefix}_TASK_${suffix}`;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Documentation"
        title={PAGE_QUERY_ACCELERATION_SQL}
        subtitle="The SQL that creates, refreshes, reads, and operates the aggregate tables behind query acceleration. Learn the pattern, then adapt it."
      />
      <Notice>
        Acceleration pre-materializes the <code>ACCOUNT_USAGE</code> views the dashboard reads into
        scheduled aggregate tables, so pages avoid cold-start scans. There are three source classes:
        <ul className="list-disc pl-5 pt-1">
          <li>
            <strong>Windowed aggregate</strong> tables are served through the{" "}
            <strong>prior UTC day</strong> (today&apos;s mutable rows stay on the live source).
          </li>
          <li>
            The <strong>global</strong> Model Rates aggregate is computed over a fixed 90-day
            window, not the dashboard date range.
          </li>
          <li>
            <strong>Cortex Inference API</strong> is never materialized. It always reads live{" "}
            <code>ACCOUNT_USAGE</code> because its per-model rate literals require live data.
          </li>
        </ul>
        Provision all of this from SQL with <code>sql/config.sql</code>, or from{" "}
        <strong>Acceleration Settings</strong>.
      </Notice>
      <Segmented
        label="Surface"
        hideLabel
        options={keys}
        value={surfaceKey}
        onChange={(v) => void setSurface(v)}
      />
      <div className="flex items-center gap-2">
        <strong>{surfaceKey}</strong>
        <Badge variant="secondary">
          {entry.objectType === "GLOBAL_AGGREGATE" ? "Global aggregate" : "Windowed aggregate"}
        </Badge>
      </div>
      <Caption>{entry.explanation}</Caption>
      <div className="text-sm">
        <strong>Source views</strong>
        <ul>
          {entry.sourceViews.map((s) => (
            <li key={s}>
              <code>{s}</code>
            </li>
          ))}
        </ul>
      </div>
      {statement && (
        <Section title="What the dashboard reads now">
          {isDt && statement.served ? (
            <Caption>
              Reading the scheduled table through {statement.served.end} (the prior UTC day).
            </Caption>
          ) : isDt ? (
            <Caption>Reading the global aggregate table.</Caption>
          ) : (
            <Caption>
              Acceleration is not active for this surface yet, so the dashboard reads live{" "}
              <code>ACCOUNT_USAGE</code>. Provision and enable it to serve from the table below.
            </Caption>
          )}
          <CodeBlock code={inlineParams(statement.sql, statement.params)} lineNumbers />
        </Section>
      )}
      <Section title="How the aggregate is built">
        <Caption>
          The scheduled task runs this body and writes the result into the aggregate table. It is
          the windowless form of the surface&apos;s fact query: the same SELECT without the date
          predicate, so the table holds all retained history and the app filters it per window.
        </Caption>
        <CodeBlock code={body} lineNumbers />
      </Section>
      <Expander title="Create the aggregate table">
        <Caption>
          Created empty with the exact column contract via{" "}
          <code>CREATE TABLE AS SELECT ... WHERE 1 = 0</code>, so the schema is fixed regardless of
          later body changes.
        </Caption>
        <CodeBlock code={lifecycle["Create aggregate table"]} lineNumbers />
      </Expander>
      <Expander title="Create the nightly refresh task">
        <Caption>
          A Snowflake Task runs a daily <code>INSERT OVERWRITE</code> at 01:00 UTC over the retained
          window, so each refresh fully replaces the table with current history.
        </Caption>
        <CodeBlock code={lifecycle["Create refresh task"]} lineNumbers />
        <Caption>Task warehouse DDL:</Caption>
        <CodeBlock code={lifecycle["Create task warehouse"]} lineNumbers />
      </Expander>
      <Expander title="Operate the task">
        <p className="text-sm">
          <strong>Enable the schedule</strong> (tasks are created suspended):
        </p>
        <CodeBlock code={lifecycle["Enable schedule"]} />
        <p className="text-sm">
          <strong>Run once now</strong> (populate immediately instead of waiting for 01:00 UTC):
        </p>
        <CodeBlock code={lifecycle["Run now"]} />
        <p className="text-sm">
          <strong>Pause the schedule</strong> (stop refreshing without dropping data):
        </p>
        <CodeBlock code={lifecycle["Pause schedule"]} />
        <Notice tone="warning">
          Destructive, for reference only. Dropping the task stops refreshes; dropping the table
          discards the materialized data and the dashboard reverts to live{" "}
          <code>ACCOUNT_USAGE</code>. Prefer teardown in Acceleration Settings, which drops only
          app-owned objects.
        </Notice>
        <CodeBlock code={lifecycle["Drop resource"]} />
      </Expander>
      <Expander title="Validate the snapshot">
        <Caption>
          After a refresh, confirm the table has rows and how current it is, and inspect recent task
          runs. These are read-only checks.
        </Caption>
        <CodeBlock code={`SELECT COUNT(*) AS rows FROM ${tableFqn};`} />
        <CodeBlock code={`SELECT MAX(USAGE_DATE) AS latest_day FROM ${tableFqn};`} />
        <CodeBlock
          code={
            "SELECT NAME, STATE, SCHEDULED_TIME, COMPLETED_TIME\n" +
            `FROM TABLE(${settings.database}.INFORMATION_SCHEMA.TASK_HISTORY(\n` +
            "  SCHEDULED_TIME_RANGE_START => DATEADD('day', -2, CURRENT_TIMESTAMP()),\n" +
            "  RESULT_LIMIT => 100))\n" +
            `WHERE NAME = '${taskName}'\n` +
            "ORDER BY SCHEDULED_TIME DESC;"
          }
        />
      </Expander>
      <Caption>
        Every statement above is generated from the same builders Acceleration Settings and{" "}
        <code>sql/config.sql</code> use, so it matches what the app provisions.
      </Caption>
    </div>
  );
}
