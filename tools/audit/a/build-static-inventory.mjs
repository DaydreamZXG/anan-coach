import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { visualScenarios } from "../../../apps/wechat/e2e/visual-scenarios.mjs";
import { pageInteractionMatrix } from "../../../apps/wechat/e2e/page-interaction-matrix.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const projectRoot = path.resolve(repositoryRoot, "apps/wechat");
const miniprogramRoot = path.resolve(projectRoot, "miniprogram");
const outputRoot = path.resolve(repositoryRoot, "audit/subagents/a");

const appConfig = JSON.parse(await readFile(path.resolve(miniprogramRoot, "app.json"), "utf8"));
const routeManifest = JSON.parse(
  await readFile(
    path.resolve(miniprogramRoot, "app/navigation/routes.manifest.json"),
    "utf8",
  ),
);
const routeRegistrySource = await readFile(
  path.resolve(miniprogramRoot, "app/navigation/routes.ts"),
  "utf8",
);

const mainPages = appConfig.pages ?? [];
const subpackagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
  (subpackage) =>
    subpackage.pages.map((pageRoute) => ({
      route: `${subpackage.root.replace(/\/$/u, "")}/${pageRoute}`,
      name: subpackage.name ?? subpackage.root,
      root: subpackage.root,
    })),
);
const registeredPages = [
  ...mainPages.map((route) => ({ route, packageName: "main", packageRoot: "" })),
  ...subpackagePages.map(({ route, name, root }) => ({
    route,
    packageName: name,
    packageRoot: root,
  })),
];
const registeredRouteSet = new Set(registeredPages.map(({ route }) => route));

const routeRegistryEntries = new Map();
for (const match of routeRegistrySource.matchAll(
  /^\s*([A-Za-z_$][\w$]*):\s*"(\/(?:pages|packages)\/[^"]+)",?\s*$/gmu,
)) {
  routeRegistryEntries.set(match[2].replace(/^\//u, ""), match[1]);
}

const sourceFiles = (
  await walkFiles(miniprogramRoot, (filename) =>
    /\.(?:ts|js|json|wxml)$/u.test(filename),
  )
).filter(
  (filename) =>
    !filename.includes(`${path.sep}api${path.sep}generated${path.sep}`) &&
    !filename.endsWith(`${path.sep}app.json`) &&
    !filename.endsWith(`${path.sep}routes.manifest.json`) &&
    !filename.endsWith(`${path.sep}routes.ts`),
);
const sourceByFile = new Map(
  await Promise.all(
    sourceFiles.map(async (filename) => [
      path.relative(repositoryRoot, filename),
      await readFile(filename, "utf8"),
    ]),
  ),
);

const directReferences = new Map();
const typedReferences = new Map();
const unregisteredLiteralReferences = [];
for (const [relativeFilename, source] of sourceByFile) {
  for (const match of source.matchAll(
    /["'`](\/(?:pages|packages|workspaces)\/[A-Za-z0-9_./-]+\/index)(?:\?[^"'`]*)?["'`]/gu,
  )) {
    const route = match[1].replace(/^\//u, "");
    appendReference(directReferences, route, relativeFilename);
    if (!registeredRouteSet.has(route)) {
      unregisteredLiteralReferences.push({
        route,
        file: relativeFilename,
        line: lineNumber(source, match.index ?? 0),
        literal: match[0],
      });
    }
  }

  for (const match of source.matchAll(/\broutes\.([A-Za-z_$][\w$]*)\b/gu)) {
    const routeEntry = [...routeRegistryEntries.entries()].find(([, key]) => key === match[1]);
    if (routeEntry) appendReference(typedReferences, routeEntry[0], relativeFilename);
  }
}

const diskPageRoutes = [];
for (const configFilename of await walkFiles(miniprogramRoot, (filename) =>
  filename.endsWith(`${path.sep}index.json`),
)) {
  const config = JSON.parse(await readFile(configFilename, "utf8"));
  if (config.component === true) continue;
  const pageRoot = configFilename.slice(0, -".json".length);
  if (
    ["ts", "wxml", "wxss"].every((extension) => existsSync(`${pageRoot}.${extension}`))
  ) {
    diskPageRoutes.push(path.relative(miniprogramRoot, pageRoot).split(path.sep).join("/"));
  }
}
diskPageRoutes.sort();
const diskPageSet = new Set(diskPageRoutes);

const visualScenarioCount = new Map();
const representativeScenario = new Map();
for (const scenario of visualScenarios) {
  const route = scenario.route.split("?", 1)[0].replace(/^\//u, "");
  visualScenarioCount.set(route, (visualScenarioCount.get(route) ?? 0) + 1);
  if (!representativeScenario.has(route)) {
    representativeScenario.set(route, {
      id: scenario.id,
      url: scenario.route,
      authenticated: scenario.authenticated === true,
    });
  }
}
const interactionRouteSet = new Set(pageInteractionMatrix.map(({ route }) => route));
const manifestRouteSet = new Set(routeManifest.registeredPages ?? []);
const tabRouteSet = new Set((appConfig.tabBar?.list ?? []).map(({ pagePath }) => pagePath));

const parameterContracts = new Map([
  ["pages/login/index", { valid: "returnTo 可选", missing: "合法" }],
  ["pages/anan/index", { valid: "taskId/track/taskType/title 可选上下文", missing: "合法" }],
  [
    "pages/positions/index",
    { valid: "score=1 可选（打开成绩单）", missing: "合法" },
  ],
  [
    "pages/positions/search/index",
    { valid: "q/examYear/region/city 可选筛选", missing: "合法" },
  ],
  ["pages/positions/detail/index", { valid: "positionId=position-1", missing: "应就地提示" }],
  [
    "pages/positions/compare/index",
    { valid: "ids=position-1,position-2", missing: "应就地提示" },
  ],
  ["pages/positions/goal/index", { valid: "positionId=position-1", missing: "应就地提示" }],
  [
    "pages/positions/briefing/index",
    { valid: "positionId=position-1", missing: "应就地提示" },
  ],
  [
    "xingce/xingce-task-intro/index",
    {
      valid: "mode=verification&planTaskId=visual-plan-task-001",
      missing: "应拒绝猜测任务",
    },
  ],
  [
    "pages/practice/index",
    {
      valid: "mode=targeted&planTaskId=visual-plan-task-001",
      missing: "应显示地址不完整",
    },
  ],
  [
    "pages/mock-exam/index",
    {
      valid: "sessionId=visual-mock-session-v1（或 planTaskId，二选一）",
      missing: "允许创建/恢复默认模考，需运行验证",
    },
  ],
  [
    "pages/reflection/index",
    {
      valid: "sessionId=visual-session-001&reviewId=visual-review-001",
      missing: "应显示地址不完整",
    },
  ],
  [
    "community/community-draft/index",
    { valid: "reviewId/track/taskVersionId 可选", missing: "合法" },
  ],
  [
    "community/community-structure/index",
    { valid: "cardId=visual-community-card", missing: "应显示地址不完整" },
  ],
  [
    "community/community-trial/index",
    { valid: "track=xingce&applicability=资料分析", missing: "需运行验证" },
  ],
  [
    "pages/essay-task/index",
    {
      valid: "stage + taskVersionId + planTaskId（阶段相关版本参数）",
      missing: "应显示地址不完整",
    },
  ],
  [
    "essay/essay-review/index",
    { valid: "taskVersionId=visual-essay-task（reviewVersionId 可选）", missing: "应就地提示" },
  ],
  [
    "essay/essay-rewrite/index",
    { valid: "taskVersionId=visual-essay-task", missing: "应就地提示" },
  ],
  [
    "essay/essay-material/index",
    { valid: "taskVersionId=visual-essay-task", missing: "应就地提示" },
  ],
  [
    "essay/essay-compose/index",
    {
      valid: "taskVersionId=visual-essay-task&planTaskId=visual-plan",
      missing: "应就地提示",
    },
  ],
  [
    "essay/essay-evidence/index",
    {
      valid: "taskVersionId=visual-essay-task（reviewVersionId 可选）",
      missing: "应就地提示",
    },
  ],
  [
    "reports/ability-module/index",
    { valid: "track=xingce&moduleKey=资料分析", missing: "应就地提示" },
  ],
  [
    "reports/error-pattern/index",
    { valid: "patternId=<64 hex>", missing: "应就地提示" },
  ],
  [
    "reports/mock-report/index",
    { valid: "sessionId 可选（默认 current）", missing: "合法/默认 current" },
  ],
  [
    "reports/report-overview/index",
    { valid: "kind=weekly 可选", missing: "合法/默认周期" },
  ],
  [
    "pages/capture-import/index",
    { valid: "importId/channel 可选", missing: "合法" },
  ],
  [
    "xingce/learning-packets/index",
    { valid: "contentVersionId 可选", missing: "合法" },
  ],
  [
    "xingce/answer-ingestion/index",
    {
      valid: "packetVersionId=packet-v3（或 ingestionId，二选一）",
      missing: "需运行验证",
    },
  ],
  [
    "pages/private-knowledge/detail/index",
    { valid: "id=source-1", missing: "当前实现 navigateBack" },
  ],
]);

const rows = [];
for (const registered of registeredPages) {
  const { route, packageName, packageRoot } = registered;
  const artifactMissing = ["ts", "json", "wxml", "wxss"].filter(
    (extension) => !existsSync(path.resolve(miniprogramRoot, `${route}.${extension}`)),
  );
  const directFiles = unique(directReferences.get(route) ?? []);
  const typedFiles = unique(typedReferences.get(route) ?? []);
  const inboundFiles = unique([...directFiles, ...typedFiles]).filter(
    (filename) => !filename.endsWith(`${route}.ts`),
  );
  const isEntry = route === mainPages[0];
  const isTab = tabRouteSet.has(route);
  const parameterContract = parameterContracts.get(route) ?? {
    valid: "无必需参数",
    missing: "合法",
  };
  const representative = representativeScenario.get(route) ?? null;
  const sources = ["ts", "json", "wxml", "wxss"]
    .map((extension) => path.resolve(miniprogramRoot, `${route}.${extension}`))
    .filter(existsSync);
  const fingerprint = createHash("sha256");
  for (const filename of sources) {
    fingerprint.update((await readFile(filename, "utf8")).replace(/\s+/gu, " ").trim());
  }
  rows.push({
    route,
    packageName,
    packageRoot,
    isEntry,
    isTab,
    diskPage: diskPageSet.has(route),
    missingArtifacts: artifactMissing,
    typedRouteKey: routeRegistryEntries.get(route) ?? "",
    inRouteManifest: manifestRouteSet.has(route),
    directInboundFiles: directFiles,
    typedInboundFiles: typedFiles,
    inboundFiles,
    orphanCandidate: !isEntry && !isTab && inboundFiles.length === 0,
    visualScenarioCount: visualScenarioCount.get(route) ?? 0,
    interactionContract: interactionRouteSet.has(route),
    representativeScenario: representative,
    requiredParameters: parameterContract.valid,
    missingParameterExpectation: parameterContract.missing,
    implementationFingerprint: fingerprint.digest("hex"),
  });
}

const fingerprints = new Map();
for (const row of rows) appendReference(fingerprints, row.implementationFingerprint, row.route);
const exactDuplicateImplementations = [...fingerprints.entries()]
  .filter(([, routes]) => routes.length > 1)
  .map(([fingerprint, routes]) => ({ fingerprint, routes }));

const result = {
  generatedAt: new Date().toISOString(),
  commit: await readGitCommit(repositoryRoot),
  counts: {
    mainRegistered: mainPages.length,
    subpackageRegistered: subpackagePages.length,
    totalRegistered: registeredPages.length,
    uniqueRegistered: registeredRouteSet.size,
    diskPages: diskPageRoutes.length,
    visualScenarios: visualScenarios.length,
    visualScenarioRoutes: visualScenarioCount.size,
    interactionContracts: pageInteractionMatrix.length,
    typedRoutes: routeRegistryEntries.size,
    routeManifestPages: manifestRouteSet.size,
  },
  registeredPages: rows,
  diskOnlyPages: diskPageRoutes.filter((route) => !registeredRouteSet.has(route)),
  registeredMissingOnDisk: [...registeredRouteSet].filter((route) => !diskPageSet.has(route)),
  manifestOnlyPages: [...manifestRouteSet].filter((route) => !registeredRouteSet.has(route)),
  registeredMissingFromManifest: [...registeredRouteSet].filter(
    (route) => !manifestRouteSet.has(route),
  ),
  unregisteredLiteralReferences,
  exactDuplicateImplementations,
};

await mkdir(outputRoot, { recursive: true });
await writeFile(
  path.resolve(outputRoot, "static-inventory.json"),
  `${JSON.stringify(result, null, 2)}\n`,
);

const csvRows = [
  [
    "route",
    "package_name",
    "package_root",
    "entry",
    "tab",
    "disk_page",
    "missing_artifacts",
    "typed_route_key",
    "in_route_manifest",
    "inbound_reference_count",
    "inbound_files",
    "orphan_candidate",
    "visual_scenario_count",
    "interaction_contract",
    "representative_scenario",
    "valid_url",
    "required_parameters",
    "missing_parameter_expectation",
  ],
  ...rows.map((row) => [
    row.route,
    row.packageName,
    row.packageRoot,
    row.isEntry,
    row.isTab,
    row.diskPage,
    row.missingArtifacts.join("|"),
    row.typedRouteKey,
    row.inRouteManifest,
    row.inboundFiles.length,
    row.inboundFiles.join("|"),
    row.orphanCandidate,
    row.visualScenarioCount,
    row.interactionContract,
    row.representativeScenario?.id ?? "",
    row.representativeScenario?.url ?? `/${row.route}`,
    row.requiredParameters,
    row.missingParameterExpectation,
  ]),
];
await writeFile(
  path.resolve(outputRoot, "static-page-inventory.csv"),
  `${csvRows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`,
);

process.stdout.write(`${JSON.stringify(result.counts, null, 2)}\n`);
process.stdout.write(`diskOnlyPages=${JSON.stringify(result.diskOnlyPages)}\n`);
process.stdout.write(
  `registeredMissingOnDisk=${JSON.stringify(result.registeredMissingOnDisk)}\n`,
);
process.stdout.write(`manifestOnlyPages=${JSON.stringify(result.manifestOnlyPages)}\n`);
process.stdout.write(
  `registeredMissingFromManifest=${JSON.stringify(result.registeredMissingFromManifest)}\n`,
);
process.stdout.write(
  `unregisteredLiteralReferences=${JSON.stringify(result.unregisteredLiteralReferences, null, 2)}\n`,
);
process.stdout.write(
  `exactDuplicateImplementations=${JSON.stringify(result.exactDuplicateImplementations)}\n`,
);

async function walkFiles(directory, predicate) {
  const entries = await readdir(directory);
  const output = [];
  for (const entry of entries) {
    const filename = path.resolve(directory, entry);
    const metadata = await stat(filename);
    if (metadata.isDirectory()) output.push(...(await walkFiles(filename, predicate)));
    else if (predicate(filename)) output.push(filename);
  }
  return output;
}

function appendReference(map, key, value) {
  const values = map.get(key) ?? [];
  values.push(value);
  map.set(key, values);
}

function unique(values) {
  return [...new Set(values)].sort();
}

function lineNumber(source, offset) {
  return source.slice(0, offset).split("\n").length;
}

function csvCell(value) {
  const normalized = String(value ?? "");
  return `"${normalized.replaceAll('"', '""')}"`;
}

async function readGitCommit(root) {
  const gitDirectory = path.resolve(root, ".git");
  const head = (await readFile(path.resolve(gitDirectory, "HEAD"), "utf8")).trim();
  if (!head.startsWith("ref: ")) return head;
  const reference = head.slice("ref: ".length);
  return (await readFile(path.resolve(gitDirectory, reference), "utf8")).trim();
}
