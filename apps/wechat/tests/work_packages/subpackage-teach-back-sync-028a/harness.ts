import { readFileSync } from "node:fs";

// @ts-expect-error The executable ESM manifest has no TypeScript declaration file.
import { subpackageSharedFiles } from "../../../scripts/subpackage-shared-manifest.mjs";
// @ts-expect-error The executable ESM generator has no TypeScript declaration file.
import * as subpackageGenerator from "../../../scripts/sync-subpackage-shared.mjs";

export interface SharedManifestEntry {
  source: string;
  path: string;
  packages: string[];
}

interface GeneratedTarget {
  entry: SharedManifestEntry;
  packageName: string;
  packageIndex: number;
  targetRelative: string;
}

export const manifestEntries = subpackageSharedFiles as unknown as SharedManifestEntry[];
export const generator = subpackageGenerator as unknown as {
  generatedTargetPlan(entries: SharedManifestEntry[]): GeneratedTarget[];
  expectedGeneratedTarget(entry: SharedManifestEntry, packageName: string): Promise<string>;
  generatedTargetIsStale(expected: string, actual: string): boolean;
  subpackageSharedPlanViolations(entries: SharedManifestEntry[]): string[];
};

export const xingceLearningEntry = requiredEntry();
export const xingceLearningTargets = generator
  .generatedTargetPlan([xingceLearningEntry])
  .map((target) => ({ ...target }));

export function neutralSource(): string {
  return readFileSync(xingceLearningEntry.source, "utf8");
}

export async function expectedTarget(target: GeneratedTarget): Promise<string> {
  return generator.expectedGeneratedTarget(target.entry, target.packageName);
}

export function actualTarget(target: GeneratedTarget): string {
  return readFileSync(`miniprogram/${target.targetRelative}`, "utf8");
}

function requiredEntry(): SharedManifestEntry {
  const entry = manifestEntries.find(({ path }) => path === "api/xingce-learning.ts");
  if (!entry) throw new Error("Expected the xingce-learning shared manifest entry.");
  return entry;
}
