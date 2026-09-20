import type { Severity } from "@/lib/types";

// Severity is weight + a marker, not three more colours.
const MARK: Record<Severity, string> = { high: "●", medium: "◐", low: "○" };
const CLASS: Record<Severity, string> = {
  high: "font-semibold text-error",
  medium: "font-medium text-ink",
  low: "text-ink-3",
};

export function Sev({ s }: { s: Severity }) {
  return (
    <span className={`mono inline-flex items-center gap-1 ${CLASS[s]}`} title={s}>
      <span aria-hidden>{MARK[s]}</span>
      {s.toUpperCase()}
    </span>
  );
}
