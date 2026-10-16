"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

// st.code equivalent: monospace block with optional line numbers and a copy button.
export function CodeBlock({ code, lineNumbers = false }: { code: string; lineNumbers?: boolean }) {
  const [copied, setCopied] = useState(false);
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className="relative rounded-lg border bg-muted/40">
      <button
        type="button"
        aria-label="Copy SQL"
        onClick={async () => {
          await navigator.clipboard.writeText(code);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        className="absolute top-2 right-2 rounded-md border bg-background p-1 text-muted-foreground hover:text-foreground"
      >
        {copied ? (
          <Check className="size-3.5" aria-hidden />
        ) : (
          <Copy className="size-3.5" aria-hidden />
        )}
      </button>
      <pre className="overflow-x-auto p-3 text-xs leading-5">
        <code>
          {lines.map((line, i) => (
            <span key={i} className="block whitespace-pre">
              {lineNumbers && (
                <span className="mr-3 inline-block w-8 select-none text-right text-muted-foreground">
                  {i + 1}
                </span>
              )}
              {line}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
