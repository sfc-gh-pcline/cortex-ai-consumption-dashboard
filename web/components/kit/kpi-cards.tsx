"use client";

import { CircleHelp } from "lucide-react";

import { Expander } from "@/components/kit/layout";
import { Card } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";
import type { Totals } from "@/lib/agg/metrics";

export interface KpiCard {
  readonly label: string;
  readonly value: string;
  readonly help?: string;
  readonly caption?: string | null;
  readonly trend?: readonly number[] | null;
  readonly delta?: string | null;
}

// Tiny inline sparkline for the credits card.
function Sparkline({ values }: { values: readonly number[] }) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 24 - 2}`)
    .join(" ");
  return (
    <svg
      viewBox="0 0 100 28"
      className="h-7 w-full text-primary"
      aria-hidden
      preserveAspectRatio="none"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function KpiCardView({ card }: { card: KpiCard }) {
  return (
    <Card className="flex min-w-52 flex-1 flex-col gap-1 px-4 py-3">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <span>{card.label}</span>
        {card.help && (
          <Tooltip>
            <TooltipTrigger aria-label={`About ${card.label}`} className="inline-flex">
              <CircleHelp className="size-3.5" aria-hidden />
            </TooltipTrigger>
            <TooltipContent className="max-w-72 text-xs">{card.help}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="text-[2rem] font-semibold leading-none">{card.value}</div>
      {card.delta && <div className="text-xs text-muted-foreground">{card.delta}</div>}
      {card.trend && <Sparkline values={card.trend} />}
      {card.caption && <div className="text-xs text-muted-foreground">{card.caption}</div>}
    </Card>
  );
}

// Equal-width KPI cards; more than four fold into an expander, as ui.kpi_cards does.
export function KpiCards({ cards }: { cards: readonly KpiCard[] }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-3">
        {cards.slice(0, 4).map((card) => (
          <KpiCardView key={card.label} card={card} />
        ))}
      </div>
      {cards.length > 4 && (
        <Expander title="Additional metrics">
          <div className="flex flex-wrap gap-3">
            {cards.slice(4).map((card) => (
              <KpiCardView key={card.label} card={card} />
            ))}
          </div>
        </Expander>
      )}
    </div>
  );
}

// Shared KPI tooltips (utils/ui.py HELP_*).
export const HELP = {
  tokens:
    "All token types summed: input, output, cache-read and cache-write. " +
    "Cache-read usually dominates and bills at a fraction of the input-token rate, " +
    "which is why credits grow far more slowly than raw token counts.",
  calls:
    "One row per billable request in ACCOUNT_USAGE. Not the same as user prompts: " +
    "a single interaction can span several requests.",
  users: "Distinct users with at least one billable request in the selected window.",
  activeDays:
    "Distinct calendar days with recorded usage. Compare with the window length to " +
    "see whether consumption is steady or bursty.",
  delta:
    "Change against the previous equal-length completed UTC period; hidden unless coverage is comparable.",
};

export interface StandardCards {
  credits: KpiCard;
  tokens: KpiCard;
  calls: KpiCard;
  users: KpiCard;
}

// The four cards every surface page shows, with per-day captions phrased identically.
export function standardCards(
  totals: Totals,
  windowDays: number,
  options: {
    creditsHelp: string;
    creditsLabel?: string;
    tokensLabel?: string;
    tokensHelp?: string;
    callsLabel?: string;
    callsHelp?: string;
    usersLabel?: string;
    callNoun?: string;
    trend?: readonly number[] | null;
    delta?: string | null;
  },
): StandardCards {
  const { CREDITS: credits, TOKENS: tokens, CALLS: calls, USERS: users } = totals;
  const callNoun = options.callNoun ?? "call";
  return {
    credits: {
      label: options.creditsLabel ?? "Credits",
      value: fmtCredits(credits),
      help: options.creditsHelp,
      caption: `${fmtCredits(credits / windowDays)} observed / day (${windowDays} days)`,
      trend: options.trend ?? null,
      delta: options.delta ?? null,
    },
    tokens: {
      label: options.tokensLabel ?? "Tokens",
      value: fmtTokens(tokens),
      help: options.tokensHelp ?? HELP.tokens,
      caption: calls ? `${fmtTokens(tokens / calls)} per ${callNoun}` : null,
    },
    calls: {
      label: options.callsLabel ?? "Calls",
      value: fmtInt(calls),
      help: options.callsHelp ?? HELP.calls,
      caption: `${fmtInt(calls / windowDays)} observed / day (${windowDays} days)`,
    },
    users: {
      label: options.usersLabel ?? "Users",
      value: fmtInt(users),
      help: HELP.users,
      caption: users ? `${fmtCredits(credits / users)} credits each` : null,
    },
  };
}
