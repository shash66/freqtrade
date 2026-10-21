"use client";

import { useTheme } from "@/components/theme-provider";
import { cn } from "@/lib/format";

const options = [
  { value: "system", label: "Auto", title: "Follow system preference" },
  { value: "light", label: "Light", title: "Light theme" },
  { value: "dark", label: "Dark", title: "Dark theme" },
] as const;

export function ThemeToggle({ className }: { className?: string }) {
  const { preference, setPreference } = useTheme();
  return (
    <div role="radiogroup" aria-label="Color theme" className={cn("inline-flex rounded-md border border-border bg-surface-2 p-0.5", className)}>
      {options.map((o) => {
        const active = preference === o.value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            onClick={() => setPreference(o.value)}
            className={cn(
              "rounded-sm px-2 py-0.5 text-[11px] font-medium",
              active ? "bg-surface text-foreground shadow-sm" : "text-muted hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
