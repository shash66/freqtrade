import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "@/lib/format";

export function TableWrap({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("w-full overflow-x-auto", className)} {...props} />;
}

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return <table className={cn("w-full border-collapse text-xs", className)} {...props} />;
}

export function THead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("bg-surface-2 text-[11px] uppercase tracking-wide text-muted", className)} {...props} />;
}

export function TBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-border", className)} {...props} />;
}

export function Tr({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("hover:bg-surface-2/60", className)} {...props} />;
}

export function Th({ className, align, ...props }: ThHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={cn("whitespace-nowrap px-2 py-1.5 font-medium", align === "right" ? "text-right" : "text-left", className)}
      {...props}
    />
  );
}

export function Td({ className, align, mono, ...props }: TdHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right"; mono?: boolean }) {
  return (
    <td
      className={cn(
        "whitespace-nowrap px-2 py-1.5 align-middle",
        align === "right" ? "text-right tabular" : "text-left",
        mono && "font-mono",
        className,
      )}
      {...props}
    />
  );
}

export function SortableTh({
  label,
  field,
  sort,
  onSort,
  align,
}: {
  label: string;
  field: string;
  sort: { field: string; dir: "asc" | "desc" } | null;
  onSort: (field: string) => void;
  align?: "left" | "right";
}) {
  const active = sort?.field === field;
  const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
  return (
    <Th align={align} aria-sort={ariaSort}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={cn("inline-flex items-center gap-1 rounded-sm uppercase tracking-wide hover:text-foreground", active && "text-foreground")}
      >
        {label}
        <span aria-hidden className="text-[10px]">
          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </Th>
  );
}
