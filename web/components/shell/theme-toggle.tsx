"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

// True after hydration, false during SSR, without a setState-in-effect cycle.
const noop = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
}

// Light/dark switch; dark is the default, as in .streamlit/config.toml.
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();
  if (!mounted) return <span className="size-6" />;
  const dark = resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="rounded-md p-1 hover:bg-sidebar-accent"
    >
      {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}

// Whether the dark class is active, for chart palettes.
export function useIsDark(): boolean {
  const { resolvedTheme } = useTheme();
  const mounted = useMounted();
  return mounted ? resolvedTheme === "dark" : true;
}
