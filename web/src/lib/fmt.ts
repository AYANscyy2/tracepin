export const shortId = (id: string) => id.replace(/^0x/, "").slice(0, 10);
export const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s` : `${ms.toFixed(ms < 1 ? 2 : 0)}ms`);
export const pct = (a: number, b: number) => (b ? `${Math.round((100 * a) / b)}%` : "–");
