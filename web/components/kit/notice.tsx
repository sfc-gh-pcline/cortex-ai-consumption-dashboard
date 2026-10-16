import { AlertTriangle, CircleAlert, Info, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type Tone = "info" | "warning" | "error" | "success";

const TONES: Record<Tone, { icon: LucideIcon; className: string }> = {
  info: { icon: Info, className: "border-primary/40 bg-primary/10 text-foreground" },
  warning: { icon: AlertTriangle, className: "border-warning/50 bg-warning/10 text-foreground" },
  error: {
    icon: CircleAlert,
    className: "border-destructive/50 bg-destructive/10 text-foreground",
  },
  success: { icon: Info, className: "border-success/50 bg-success/10 text-foreground" },
};

// st.info / st.warning / st.error / st.success equivalent.
export function Notice({
  tone = "info",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  const { icon: Icon, className: toneClass } = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("flex gap-2 rounded-lg border px-3 py-2 text-sm", toneClass, className)}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 [&_code]:text-[0.85em]">{children}</div>
    </div>
  );
}

// Muted single line, the equivalent of st.caption.
export function Caption({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-xs text-muted-foreground", className)}>{children}</p>;
}
