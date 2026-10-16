"use client";

import { Check, ChevronsUpDown, X } from "lucide-react";
import { useState } from "react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface MultiSelectProps {
  readonly label: string;
  readonly options: readonly string[];
  readonly value: readonly string[];
  readonly onChange: (next: string[]) => void;
  readonly placeholder?: string;
  readonly disabled?: boolean;
  readonly help?: string;
  readonly className?: string;
}

// Searchable multi-select, the equivalent of st.multiselect with
// bind="query-params". Selected values absent from the options are kept and
// flagged rather than silently dropped, so a shared link never broadens scope.
export function MultiSelect({
  label,
  options,
  value,
  onChange,
  placeholder,
  disabled,
  help,
  className,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const invalid = value.filter((v) => !options.includes(v));
  const choices = [...new Set([...options, ...value])].sort();
  const toggle = (option: string) =>
    onChange(value.includes(option) ? value.filter((v) => v !== option) : [...value, option]);
  return (
    <div className={cn("flex min-w-44 flex-col gap-1", className)}>
      <span className="text-xs text-muted-foreground" title={help}>
        {label}
      </span>
      {invalid.length > 0 && (
        <span className="text-xs text-warning">
          {label}: selected values are absent from this view: {invalid.join(", ")}.
        </span>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          disabled={disabled}
          aria-label={label}
          className={cn(
            "flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 text-sm",
            disabled && "cursor-not-allowed opacity-50",
          )}
        >
          <span className="truncate">
            {value.length === 0
              ? (placeholder ?? `All ${label.toLowerCase()}`)
              : value.length <= 2
                ? value.join(", ")
                : `${value.length} selected`}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0" align="start">
          <Command>
            <CommandInput placeholder={`Search ${label.toLowerCase()}...`} />
            <CommandList>
              <CommandEmpty>No matches.</CommandEmpty>
              <CommandGroup>
                {choices.map((option) => (
                  <CommandItem key={option} value={option} onSelect={() => toggle(option)}>
                    <Check
                      className={cn("size-4", value.includes(option) ? "opacity-100" : "opacity-0")}
                      aria-hidden
                    />
                    <span className="truncate">{option}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
            {value.length > 0 && (
              <button
                type="button"
                onClick={() => onChange([])}
                className="flex items-center gap-1 border-t px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" aria-hidden /> Clear {label.toLowerCase()}
              </button>
            )}
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}
