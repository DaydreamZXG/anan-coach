export function getCaptureState<T extends string>(
  allowedStates: readonly T[],
  fallback: T,
): T {
  const requestedState = new URLSearchParams(window.location.search).get("view");
  return requestedState && allowedStates.includes(requestedState as T)
    ? (requestedState as T)
    : fallback;
}
