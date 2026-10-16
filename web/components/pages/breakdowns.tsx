"use client";

import { BreakdownTable, type ExtraColumn } from "@/components/kit/breakdown-table";
import { Section } from "@/components/kit/layout";
import { Caption } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { byAgentTag, byDimension, byUserTag } from "@/lib/agg/metrics";
import type { Row } from "@/lib/agg/rows";
import { useStringParam } from "@/lib/client/url-state";

export const TAG_COLUMN = "__tag__";
export const AGENT_TAG_COLUMN = "__agent_tag__";

export interface BreakdownsProps {
  readonly urlKey: string;
  readonly defaultLabel: string;
  // Display label -> fact column, or one of the tag pseudo-columns.
  readonly labels: Readonly<Record<string, string>>;
  readonly rows: readonly Row[];
  readonly tags: readonly Row[] | null;
  readonly credits: number;
  readonly callsLabel?: string;
  readonly extraColumns?: Readonly<Record<string, ExtraColumn>>;
  // Optional caption shown when a specific column is selected.
  readonly captions?: Readonly<Record<string, string>>;
}

// "Breakdowns" section with a URL-bound group-by control, shared by the surface pages.
export function Breakdowns({
  urlKey,
  defaultLabel,
  labels,
  rows,
  tags,
  credits,
  callsLabel = "Usage records",
  extraColumns,
  captions = {},
}: BreakdownsProps) {
  const [choiceParam, setChoice] = useStringParam(urlKey, defaultLabel);
  const choice = choiceParam in labels ? choiceParam : defaultLabel;
  const column = labels[choice];
  let content;
  if (column === TAG_COLUMN) {
    content = (
      <>
        <BreakdownTable
          breakdown={byUserTag(rows, tags)}
          label="User tag"
          labelCol="TAG_LABEL"
          denominator={credits}
          callsLabel={callsLabel}
        />
        <Caption>
          {captions[TAG_COLUMN] ?? "Tag groups overlap; their shares are not additive."}
        </Caption>
      </>
    );
  } else if (column === AGENT_TAG_COLUMN) {
    content = (
      <>
        <BreakdownTable
          breakdown={byAgentTag(rows)}
          label="Agent tag"
          labelCol="TAG_LABEL"
          denominator={credits}
          callsLabel={callsLabel}
        />
        <Caption>Tag groups overlap; their shares are not additive.</Caption>
      </>
    );
  } else {
    content = (
      <>
        <BreakdownTable
          breakdown={byDimension(rows, column)}
          label={choice.replace("By ", "").replace(/^\w/, (c) => c.toUpperCase())}
          callsLabel={callsLabel}
          extraColumns={extraColumns}
        />
        {captions[column] && <Caption>{captions[column]}</Caption>}
      </>
    );
  }
  return (
    <Section title="Breakdowns">
      <Segmented
        label="Group by"
        hideLabel
        options={Object.keys(labels)}
        value={choice}
        onChange={(v) => void setChoice(v)}
      />
      {content}
    </Section>
  );
}
