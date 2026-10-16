"use client";

import { cn } from "@/lib/utils";

interface SegmentedProps<T extends string> {
  readonly label: string;
  readonly options: readonly T[];
  readonly value: T;
  readonly onChange: (value: T) => void;
  readonly hideLabel?: boolean;
  readonly className?: string;
}

// Single-choice pill group, the equivalent of st.segmented_control / st.pills.
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  hideLabel,
  className,
}: SegmentedProps<T>) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className={cn("text-xs text-muted-foreground", hideLabel && "sr-only")}>{label}</span>
      <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1">
        {options.map((option) => {
          const selected = option === value;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(option)}
              className={cn(
                "rounded-full border px-3 py-1 text-sm transition-colors",
                selected
                  ? "border-primary bg-primary/15 text-foreground"
                  : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {option}
            </button>
          );
        })}
      </div>
    </div>
  );
}
