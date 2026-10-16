"use client";

import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Caption } from "@/components/kit/notice";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// Report-style page header: eyebrow, title, subtitle, surface pills.
export function PageHeader({
  title,
  subtitle,
  pills,
  eyebrow,
}: {
  title: string;
  subtitle: string;
  pills?: readonly string[];
  eyebrow?: string;
}) {
  return (
    <header className="flex flex-col gap-1">
      {eyebrow && (
        <span className="text-[11px] font-semibold uppercase tracking-wide text-primary">
          {eyebrow}
        </span>
      )}
      <h1>{title}</h1>
      <Caption className="text-sm">{subtitle}</Caption>
      {pills && pills.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {pills.map((pill) => (
            <Badge key={pill} variant="secondary">
              {pill}
            </Badge>
          ))}
        </div>
      )}
    </header>
  );
}

// Unbordered section with an H2, like ui.section.
export function Section({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return (
    <section id={`section-${slug}`} className={cn("flex flex-col gap-3", className)}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

// st.expander equivalent.
export function Expander({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium hover:bg-muted/50"
      >
        {title}
        <ChevronDown
          className={cn("size-4 transition-transform", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && <div className="flex flex-col gap-3 border-t px-3 py-3">{children}</div>}
    </div>
  );
}

// Concise driver observation, collapsed by default like ui.insight_banner.
export function InsightBanner({ detail }: { detail: string }) {
  return (
    <Expander title="Leading driver">
      <Caption>{detail}</Caption>
    </Expander>
  );
}

// Horizontal row that wraps on narrow screens.
export function Row({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap items-end gap-3", className)}>{children}</div>;
}
