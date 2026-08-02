/**
 * Normalize binary values returned by native WeChat APIs.
 *
 * DevTools/native values can come from another JavaScript realm, where a real
 * ArrayBuffer fails the local `instanceof ArrayBuffer` check. Some runtimes may
 * also expose a typed-array view. Copying into the current realm gives hashing
 * and upload code one stable representation.
 */
export function normalizeArrayBuffer(value: unknown): ArrayBuffer | null {
  if (value instanceof ArrayBuffer) return value;

  if (ArrayBuffer.isView(value)) {
    const view = value;
    return new Uint8Array(view.buffer, view.byteOffset, view.byteLength).slice().buffer;
  }

  if (Object.prototype.toString.call(value) === "[object ArrayBuffer]") {
    try {
      return new Uint8Array(value as ArrayBuffer).slice().buffer;
    } catch {
      return null;
    }
  }

  return null;
}
