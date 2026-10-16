"use client";

import { CodeBlock } from "@/components/kit/code-block";
import { Expander, PageHeader, Section } from "@/components/kit/layout";
import { Caption, Notice } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { useDashboard } from "@/lib/client/dashboard";
import { useDependencies } from "@/lib/client/queries";
import { useStringParam } from "@/lib/client/url-state";
import { rowsAsRates } from "@/lib/rates";
import { inlineParams } from "@/lib/sql/core";
import {
  SHARED,
  SQL_REFERENCE,
  sqlReferenceFor,
  sqlReferenceGroups,
  type SqlReference,
} from "@/lib/sql/reference";
import { SURFACE_MODEL_RATES } from "@/lib/plans";

export function ReferenceSqlPage() {
  const { displayWindow, today, loadWindow, settings } = useDashboard();
  const groups = sqlReferenceGroups();
  const [groupParam, setGroup] = useStringParam("sql_group", groups[0]);
  const group = groups.includes(groupParam) ? groupParam : groups[0];
  const entries = sqlReferenceFor(group);
  const variants = entries.map((e) => e.variant);
  const [variantParam, setVariant] = useStringParam(`sql_variant_${group}`, variants[0]);
  const entry = entries.find((e) => e.variant === variantParam) ?? entries[0];
  // The REST statement binds observed model rates, so it needs the rate snapshot.
  const needsRates = entry.runtimeKey === "Cortex Inference API";
  const [rates] = useDependencies([SURFACE_MODEL_RATES], loadWindow, settings, needsRates);
  const context = {
    today: today ?? "1970-01-01",
    rates: rates.data ? rowsAsRates(rates.data.rows) : [],
  };

  const render = (item: SqlReference) => {
    const statement = displayWindow
      ? item.builder(displayWindow.start, displayWindow.end, context)
      : null;
    return (
      <div className="flex flex-col gap-2">
        <Caption>{item.note}</Caption>
        <div className="text-sm">
          <strong>Source views</strong>
          <ul className="list-none pl-0">
            {item.sources.map((s) => (
              <li key={s}>
                <code>{s}</code>
              </li>
            ))}
          </ul>
        </div>
        {statement && (
          <CodeBlock code={inlineParams(statement.sql, statement.params)} lineNumbers />
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Documentation"
        title="Reference SQL"
        subtitle="The baseline ACCOUNT_USAGE queries behind every page, for the selected window."
      />
      <Notice>
        These are the queries the dashboard runs when query acceleration is <strong>not</strong>{" "}
        enabled, read directly from <code>SNOWFLAKE.ACCOUNT_USAGE</code>. They are generated from
        the same builders the dashboard executes, so they cannot drift from what produced the
        numbers. Bound date parameters are substituted inline, so each one can be pasted into a
        worksheet and run as-is. All timestamps are bucketed on <strong>UTC</strong> calendar days
        via <code>CONVERT_TIMEZONE</code>, because these views mix <code>TIMESTAMP_TZ</code> and{" "}
        <code>TIMESTAMP_LTZ</code> columns. When acceleration is on, see the{" "}
        <strong>Query Acceleration SQL</strong> page for the scheduled-table reads that replace
        these.
      </Notice>
      {displayWindow && (
        <Caption>
          Window: {displayWindow.start} to {displayWindow.end} · change it above to regenerate
          these.
        </Caption>
      )}
      <div className="text-sm font-medium">Core surface queries</div>
      <Segmented
        label="Surface"
        hideLabel
        options={groups}
        value={group}
        onChange={(v) => void setGroup(v)}
      />
      {entries.length > 1 && (
        <Segmented
          label={`${group} statement`}
          hideLabel
          options={variants}
          value={entry.variant}
          onChange={(v) => void setVariant(v)}
        />
      )}
      <Section title={entry.kind ? entry.label : `${group} · ${entry.variant}`}>
        {entry.kind && <Caption>{entry.kind}</Caption>}
        {render(entry)}
      </Section>
      <Expander title={`Shared building blocks (${sqlReferenceFor(SHARED).length})`}>
        <Caption>
          Statements that back more than one page. They are run once and their results reused,
          rather than repeated inside each surface&apos;s query.
        </Caption>
        {sqlReferenceFor(SHARED).map((item) => (
          <Section key={item.label} title={item.label}>
            <Caption>{item.kind}</Caption>
            {render(item)}
          </Section>
        ))}
      </Expander>
      <Section title="How Credits Are Derived">
        <table className="text-sm">
          <thead>
            <tr className="text-left">
              <th className="pr-4 pb-1">Surface</th>
              <th className="pr-4 pb-1">Credit source</th>
              <th className="pb-1">Exact or estimated</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["CoCo", "TOKEN_CREDITS", "Exact, reported by Snowflake"],
              ["Cortex AI Functions", "CREDITS", "Exact, reported by Snowflake"],
              ["Cortex Inference API", "derived from tokens × observed model rates", "Estimated"],
              ["Cortex Agents", "TOKEN_CREDITS", "Exact, reported by Snowflake"],
              [
                "CoWork",
                "TOKEN_CREDITS plus METADATA:sql_query_credits and METADATA:ai_functions_credits",
                "Exact, reported by Snowflake",
              ],
            ].map(([surface, source, kind]) => (
              <tr key={surface} className="border-t">
                <td className="py-1 pr-4">{surface}</td>
                <td className="py-1 pr-4">
                  <code>{source}</code>
                </td>
                <td className="py-1">{kind === "Estimated" ? <strong>{kind}</strong> : kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Caption>
          Cortex Agents and CoWork do not overlap: per Snowflake&apos;s docs, requests originating
          from Cortex Agents are excluded from SNOWFLAKE_COWORK_USAGE_HISTORY and recorded in
          CORTEX_AGENT_USAGE_HISTORY, so the two can be summed without double counting.
        </Caption>
      </Section>
      <span className="hidden">{SQL_REFERENCE.length}</span>
    </div>
  );
}
