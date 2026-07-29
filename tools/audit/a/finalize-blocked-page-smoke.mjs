import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const outputRoot = path.resolve(repositoryRoot, "audit/subagents/a");
const inventory = JSON.parse(
  await readFile(path.resolve(outputRoot, "static-inventory.json"), "utf8"),
);
const runtimeResult = JSON.parse(
  await readFile(path.resolve(outputRoot, "logs/route-smoke-results.json"), "utf8"),
);

const globalBlocker =
  "Wechat DevTools automation did not establish a page-ready App channel; no per-route run was executed.";
const evidence =
  "audit/subagents/a/logs/devtools-app-launch-excerpt.log|audit/subagents/a/logs/devtools-launch-state-3.png|audit/subagents/a/logs/route-smoke-results.json";

const rows = [
  [
    "route",
    "package_name",
    "package_root",
    "entry",
    "tab",
    "disk_page",
    "valid_url",
    "required_parameters",
    "missing_parameter_expectation",
    "actual_open_attempted",
    "open_status",
    "actual_route",
    "screenshot_path",
    "console_log_path",
    "missing_params_attempted",
    "missing_params_status",
    "back_attempted",
    "back_status",
    "blocker",
    "evidence",
  ],
  ...inventory.registeredPages.map((page) => [
    page.route,
    page.packageName,
    page.packageRoot,
    page.isEntry,
    page.isTab,
    page.diskPage,
    page.representativeScenario?.url ?? `/${page.route}`,
    page.requiredParameters,
    page.missingParameterExpectation,
    false,
    "blocked-global",
    "",
    page.isEntry ? "audit/subagents/a/logs/devtools-launch-state-3.png" : "",
    "audit/subagents/a/logs/devtools-app-launch-excerpt.log",
    false,
    "blocked-global",
    false,
    "blocked-global",
    globalBlocker,
    evidence,
  ]),
];
await writeFile(
  path.resolve(outputRoot, "page-smoke.csv"),
  `${rows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`,
);
await writeFile(
  path.resolve(outputRoot, "logs/page-smoke-blocker.json"),
  `${JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      registeredPages: inventory.counts.totalRegistered,
      actualOpenAttempted: 0,
      successfulOpen: 0,
      blocked: inventory.counts.totalRegistered,
      missingParameterRuns: 0,
      backNavigationRuns: 0,
      globalBlocker,
      finalLaunchError: runtimeResult.launchError,
      evidence: evidence.split("|"),
    },
    null,
    2,
  )}\n`,
);

process.stdout.write(
  `Wrote ${String(inventory.counts.totalRegistered)} globally blocked page rows.\n`,
);

function csvCell(value) {
  const normalized = String(value ?? "");
  return `"${normalized.replaceAll('"', '""')}"`;
}
