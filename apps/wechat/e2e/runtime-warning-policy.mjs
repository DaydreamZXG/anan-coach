const actionablePatterns = [
  /received type-uncompatible value/u,
  /wx\.(?:saveFile|removeSavedFile) 即将废弃/u,
  /Some selectors are not allowed in component wxss/u,
  /Cannot find global type ['"](?:Boolean|Number|String|Array|Object)['"]/u,
  /\b(?:GET|POST|PUT|DELETE)\b.+\/api\/v2\/.+\b(?:401|404)\b/u,
];

export function consoleEventText(event) {
  if (typeof event === "string") return event;
  try {
    return JSON.stringify(event);
  } catch {
    return String(event);
  }
}

export function isActionableRuntimeWarning(message) {
  return actionablePatterns.some((pattern) => pattern.test(message));
}

export const actionableRuntimeWarningPatterns = [...actionablePatterns];
