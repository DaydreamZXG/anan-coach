import { sha256Hex } from "../api/sha256";

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

export function stableJson(value: unknown): string {
  return canonicalize(value, new WeakSet(), "$");
}

export function stableFingerprint(value: unknown): string {
  const canonical = stableJson(value);
  return sha256Hex(utf8Buffer(canonical));
}

function canonicalize(value: unknown, ancestors: WeakSet<object>, path: string): string {
  if (value === null) return "null";

  switch (typeof value) {
    case "string":
    case "boolean":
      return JSON.stringify(value);
    case "number":
      if (!Number.isFinite(value)) {
        throw new Error(`canonical input contains a non-finite number at ${path}`);
      }
      return JSON.stringify(value);
    case "undefined":
    case "bigint":
    case "symbol":
    case "function":
      throw new Error(`canonical input contains an unsupported value at ${path}`);
    case "object":
      break;
  }

  if (ancestors.has(value)) {
    throw new Error(`canonical input contains a cycle at ${path}`);
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      assertCanonicalArray(value, path);
      return `[${value
        .map((item, index) => canonicalize(item, ancestors, `${path}[${String(index)}]`))
        .join(",")}]`;
    }

    if (!isPlainObject(value)) {
      throw new Error(`canonical input contains a non-plain object at ${path}`);
    }
    if (Object.getOwnPropertySymbols(value).length > 0) {
      throw new Error(`canonical input contains a symbol key at ${path}`);
    }

    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(
        ([key, item]) =>
          `${JSON.stringify(key)}:${canonicalize(item, ancestors, `${path}.${key}`)}`,
      )
      .join(",")}}`;
  } finally {
    ancestors.delete(value);
  }
}

function assertCanonicalArray(value: unknown[], path: string): void {
  if (Object.getOwnPropertySymbols(value).length > 0) {
    throw new Error(`canonical input contains a symbol key at ${path}`);
  }
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) {
      throw new Error(`canonical input contains a sparse array entry at ${path}`);
    }
  }
  const indexedKeys = new Set(Array.from({ length: value.length }, (_, index) => String(index)));
  const extraKey = Object.keys(value).find((key) => !indexedKeys.has(key));
  if (extraKey !== undefined) {
    throw new Error(
      `canonical input contains an unsupported array property at ${path}.${extraKey}`,
    );
  }
}

function isPlainObject(value: object): value is Record<string, unknown> {
  const prototype: unknown = Object.getPrototypeOf(value) as unknown;
  return prototype === Object.prototype || prototype === null;
}

function utf8Buffer(value: string): ArrayBuffer {
  let encoded: string;
  try {
    encoded = encodeURIComponent(value);
  } catch (error: unknown) {
    throw new Error("canonical input contains invalid Unicode", { cause: error });
  }

  const bytes: number[] = [];
  for (let index = 0; index < encoded.length; index += 1) {
    if (encoded[index] === "%") {
      bytes.push(Number.parseInt(encoded.slice(index + 1, index + 3), 16));
      index += 2;
    } else {
      bytes.push(encoded.charCodeAt(index));
    }
  }
  const buffer = new ArrayBuffer(bytes.length);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}
