/** Restore an array that WeChat exposed as a contiguous numeric-keyed property object. */
export function arrayPropertyItems<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (!isRecord(value)) return [];
  const keys = Object.keys(value);
  if (keys.length === 0) return [];
  const indexes = keys.map(Number);
  if (
    indexes.some((index) => !Number.isInteger(index) || index < 0) ||
    indexes.some((index) => String(index) !== keys[index]) ||
    Math.max(...indexes) !== keys.length - 1
  ) {
    return [];
  }
  return indexes.sort((left, right) => left - right).map((index) => value[String(index)] as T);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
