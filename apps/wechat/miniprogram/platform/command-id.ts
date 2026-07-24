export function formatIdempotencyKey(prefix: string, randomValues: ArrayBuffer): string {
  if (!/^[a-z][a-z0-9-]{1,39}$/u.test(prefix)) {
    throw new Error("idempotency key prefix is invalid");
  }
  if (randomValues.byteLength !== 16) {
    throw new Error("idempotency entropy must contain exactly 16 bytes");
  }
  const bytes = new Uint8Array(randomValues.slice(0));
  bytes[6] = ((bytes.at(6) ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes.at(8) ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  const uuid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  return `${prefix}-${uuid}`;
}

export async function createIdempotencyKey(prefix: string): Promise<string> {
  const result = await wx.getRandomValues({ length: 16 });
  return formatIdempotencyKey(prefix, result.randomValues);
}
