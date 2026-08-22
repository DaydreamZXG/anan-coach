/** 把 0..1 的比例变成人读得懂的百分比，不把 IEEE-754 尾数泄露到界面。 */
export function formatPercentRatio(value: number, maximumFractionDigits = 2): string {
  if (!Number.isFinite(value)) return "--";
  const digits = Math.max(0, Math.min(4, Math.trunc(maximumFractionDigits)));
  const rounded = Number((value * 100).toFixed(digits));
  return `${String(Object.is(rounded, -0) ? 0 : rounded)}%`;
}
