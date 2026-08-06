import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pageInteractionMatrix } from "../../apps/wechat/e2e/page-interaction-matrix.mjs";
import { visualScenarios } from "../../apps/wechat/e2e/visual-scenarios.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../..");
const wechatRoot = path.resolve(repositoryRoot, "apps/wechat");
const miniprogramRoot = path.resolve(wechatRoot, "miniprogram");
const auditRoot = path.resolve(repositoryRoot, "audit");
const openapiPath = path.resolve(auditRoot, "api-contract/openapi.json");

const appConfig = readJson(path.resolve(miniprogramRoot, "app.json"));
const mainPages = (appConfig.pages ?? []).map((route) => ({
  route,
  packageName: "main",
  packageRoot: "",
}));
const subpackagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
  (subpackage) =>
    subpackage.pages.map((pageRoute) => ({
      route: `${subpackage.root.replace(/\/$/u, "")}/${pageRoute}`,
      packageName: subpackage.name ?? subpackage.root,
      packageRoot: subpackage.root.replace(/\/$/u, ""),
    })),
);
const registeredPages = [...mainPages, ...subpackagePages];
const registeredRouteSet = new Set(registeredPages.map((page) => page.route));
const tabRoutes = new Set((appConfig.tabBar?.list ?? []).map((tab) => tab.pagePath));
const allSourceFiles = walkFiles(miniprogramRoot).filter((file) =>
  /\.(?:ts|wxml|json)$/u.test(file),
);
const testFiles = walkFiles(path.resolve(wechatRoot, "tests")).filter((file) =>
  /\.test\.ts$/u.test(file),
);
const routeRegistrySource = readText(path.resolve(miniprogramRoot, "app/navigation/routes.ts"));
const globalComponents = appConfig.usingComponents ?? {};
const scenariosByRoute = groupBy(
  visualScenarios,
  (scenario) => scenario.route.split("?", 1)[0].replace(/^\//u, ""),
);
const interactionsByRoute = new Map(
  pageInteractionMatrix.map((contract) => [contract.route, contract]),
);

const sourceCache = new Map(allSourceFiles.map((file) => [file, readText(file)]));
const testSourceCache = new Map(testFiles.map((file) => [file, readText(file)]));

const pageRows = registeredPages.map((page) => {
  const basePath = path.resolve(miniprogramRoot, page.route);
  const configPath = `${basePath}.json`;
  const templatePath = `${basePath}.wxml`;
  const controllerPath = `${basePath}.ts`;
  const pageConfig = existsSync(configPath) ? readJson(configPath) : {};
  const template = existsSync(templatePath) ? readText(templatePath) : "";
  const controller = existsSync(controllerPath) ? readText(controllerPath) : "";
  const scenarioList = scenariosByRoute.get(page.route) ?? [];
  const pageComponents = pageConfig.usingComponents ?? {};
  const components = { ...globalComponents, ...pageComponents };
  const routeLiteral = `/${page.route}`;
  const incomingReferences = [...sourceCache.entries()]
    .filter(
      ([file, source]) =>
        file !== controllerPath &&
        file !== configPath &&
        file !== templatePath &&
        source.includes(routeLiteral),
    )
    .map(([file]) => relativeRepositoryPath(file));
  const observedParams = new Set();
  for (const scenario of scenarioList) {
    const query = scenario.route.split("?", 2)[1] ?? "";
    for (const pair of query.split("&").filter(Boolean)) {
      observedParams.add(decodeURIComponent(pair.split("=", 1)[0] ?? ""));
    }
  }
  for (const match of controller.matchAll(/\b(?:options|query)\.([A-Za-z][A-Za-z0-9]*)/gu)) {
    observedParams.add(match[1]);
  }
  const apiImports = [
    ...controller.matchAll(/from\s+["']([^"']*(?:\/api\/|\/api["'])[^"']*)["']/gu),
  ].map((match) => match[1]);
  const entryKinds = [];
  if (appConfig.pages?.[0] === page.route) entryKinds.push("app-entry");
  if (tabRoutes.has(page.route)) entryKinds.push("tab");
  if (routeRegistrySource.includes(`"/${page.route}"`)) entryKinds.push("typed-route");
  if (incomingReferences.length > 0) entryKinds.push(`source-refs:${incomingReferences.length}`);
  if (page.route.startsWith("packages/")) entryKinds.push("asset-preview");
  const sharedComponentLabels = Object.entries(components).map(
    ([name, componentPath]) => `${name}:${componentPath}`,
  );

  return {
    page_name: pageTitle(page.route, pageConfig, template),
    route: `/${page.route}`,
    package: page.packageName,
    package_root: page.packageRoot || "main",
    functional_domain: functionalDomain(page.route),
    template_type: templateType(page.route, template),
    entry_points: entryKinds.join("|") || "none-found",
    required_route_params: routeParameterContract(page.route, observedParams),
    shared_components: sharedComponentLabels.join("|"),
    api_dependencies: apiImports.join("|"),
    login_required: loginRequirement(page.route, controller),
    opens_now: "pending-runtime-smoke",
    screenshot_path: "",
    audit_status: "inventory-complete;runtime-pending",
    visual_scenario_count: scenarioList.length,
    interaction_contract: interactionsByRoute.has(page.route) ? "yes" : "no",
    incoming_reference_files: incomingReferences.slice(0, 12).join("|"),
  };
});

writeCsv(path.resolve(auditRoot, "page-inventory.csv"), pageRows);

const componentRows = buildComponentInventory();
writeCsv(path.resolve(auditRoot, "component-inventory.csv"), componentRows);

if (existsSync(openapiPath)) {
  const apiRows = buildApiInventory(readJson(openapiPath));
  writeCsv(path.resolve(auditRoot, "api-inventory.csv"), apiRows);
}

const coverageRows = pageRows.map((page) => {
  const normalizedRoute = page.route.replace(/^\//u, "");
  const referencingTests = [...testSourceCache.entries()]
    .filter(([, source]) => source.includes(page.route) || source.includes(normalizedRoute))
    .map(([file]) => relativeRepositoryPath(file));
  const scenarioCount = Number(page.visual_scenario_count);
  return {
    route: page.route,
    functional_domain: page.functional_domain,
    package: page.package,
    static_inventory: "covered",
    referenced_by_tests: referencingTests.length > 0 ? "yes" : "no",
    test_file_count: referencingTests.length,
    test_files: referencingTests.join("|"),
    visual_scenario_count: scenarioCount,
    existing_visual_fixture: scenarioCount > 0 ? "yes" : "no",
    interaction_contract: page.interaction_contract,
    route_smoke: "pending",
    standard_render: "pending",
    small_screen_render: "pending",
    tall_android_render: "pending",
    tablet_portrait_render: "pending",
    tablet_landscape_render: "pending",
    core_flow_coverage: "pending",
    evidence_path: "",
    blocker: "",
  };
});
writeCsv(path.resolve(auditRoot, "coverage-matrix.csv"), coverageRows);

const mapMetadata = {
  generatedAt: new Date().toISOString(),
  gitCommit: runGitCommit(),
  registeredRouteCount: registeredPages.length,
  mainPackageRouteCount: mainPages.length,
  subpackageRouteCount: subpackagePages.length,
  productRouteCount: registeredPages.filter((page) => !page.route.startsWith("packages/")).length,
  assetPreviewRouteCount: registeredPages.filter((page) => page.route.startsWith("packages/"))
    .length,
  visualScenarioCount: visualScenarios.length,
  visualScenarioUniqueRouteCount: scenariosByRoute.size,
  interactionContractCount: pageInteractionMatrix.length,
  componentCount: componentRows.length,
  openapiPresent: existsSync(openapiPath),
};
writeFileSync(
  path.resolve(auditRoot, "project-map-metadata.json"),
  `${JSON.stringify(mapMetadata, null, 2)}\n`,
);

function buildComponentInventory() {
  const jsonFiles = walkFiles(miniprogramRoot).filter((file) => file.endsWith(".json"));
  const componentConfigs = jsonFiles
    .map((file) => ({ file, config: safeReadJson(file) }))
    .filter(({ config }) => config?.component === true);
  const jsonConsumers = jsonFiles.map((file) => ({
    file,
    config: safeReadJson(file) ?? {},
  }));

  return componentConfigs.map(({ file, config }) => {
    const componentBase = file.slice(0, -".json".length);
    const consumers = [];
    for (const consumer of jsonConsumers) {
      for (const [alias, declaredPath] of Object.entries(
        consumer.config.usingComponents ?? {},
      )) {
        const resolved = resolveComponentReference(consumer.file, String(declaredPath));
        if (resolved === componentBase) {
          consumers.push(`${relativeRepositoryPath(consumer.file)}#${alias}`);
        }
      }
    }
    const uniqueConsumers = [...new Set(consumers)].sort();
    const complete = ["ts", "json", "wxml", "wxss"].every((extension) =>
      existsSync(`${componentBase}.${extension}`),
    );
    const dependencies = Object.entries(config.usingComponents ?? {}).map(
      ([alias, declaredPath]) => `${alias}:${declaredPath}`,
    );
    return {
      component_name: path.basename(path.dirname(file)),
      path: relativeMiniprogramPath(componentBase),
      functional_domain: functionalDomain(relativeMiniprogramPath(componentBase)),
      globally_registered: uniqueConsumers.some(
        (consumer) => consumer.startsWith("apps/wechat/miniprogram/app.json#"),
      )
        ? "yes"
        : "no",
      consumer_count: uniqueConsumers.length,
      consumers: uniqueConsumers.join("|"),
      dependencies: dependencies.join("|"),
      artifact_complete: complete ? "yes" : "no",
      audit_status: "static-inventory-complete",
    };
  });
}

function buildApiInventory(openapi) {
  const frontendTypeScriptFiles = walkFiles(miniprogramRoot).filter((file) =>
    file.endsWith(".ts"),
  );
  const frontendSources = new Map(
    frontendTypeScriptFiles.map((file) => [file, readText(file)]),
  );
  const rows = [];
  for (const [apiPath, pathItem] of Object.entries(openapi.paths ?? {})) {
    for (const method of ["get", "post", "put", "patch", "delete", "options", "head"]) {
      const operation = pathItem?.[method];
      if (!operation) continue;
      const references = [...frontendSources.entries()]
        .filter(([, source]) => source.includes(apiPath))
        .map(([file]) => relativeRepositoryPath(file));
      const responseCodes = Object.keys(operation.responses ?? {});
      const successCodes = responseCodes.filter((code) => /^2/u.test(code));
      const errorCodes = responseCodes.filter((code) => !/^2/u.test(code));
      const security = operation.security ?? openapi.security ?? [];
      rows.push({
        method: method.toUpperCase(),
        path: apiPath,
        operation_id: operation.operationId ?? "",
        tags: (operation.tags ?? []).join("|"),
        summary: operation.summary ?? "",
        auth_required: Array.isArray(security) && security.length > 0 ? "yes" : "no/unspecified",
        request_body: operation.requestBody ? "yes" : "no",
        success_codes: successCodes.join("|"),
        error_codes: errorCodes.join("|"),
        deprecated: operation.deprecated ? "yes" : "no",
        frontend_literal_reference_count: references.length,
        frontend_literal_reference_files: references.slice(0, 12).join("|"),
        audit_status: "openapi-inventory-complete;contract-review-pending",
      });
    }
  }
  return rows.sort((left, right) =>
    `${left.path}:${left.method}`.localeCompare(`${right.path}:${right.method}`),
  );
}

function routeParameterContract(route, observedParams) {
  const explicit = new Map([
    ["pages/positions/detail/index", ["positionId!"]],
    ["pages/positions/compare/index", ["ids!"]],
    ["pages/positions/goal/index", ["positionId!"]],
    ["pages/positions/briefing/index", ["positionId!"]],
    ["xingce/xingce-task-intro/index", ["mode!", "planTaskId!"]],
    ["pages/practice/index", ["mode!", "planTaskId?"]],
    ["pages/mock-exam/index", ["sessionId?|planTaskId?"]],
    ["pages/reflection/index", ["sessionId!", "reviewId!"]],
    ["pages/essay-task/index", ["stage!", "taskVersionId!", "planTaskId!"]],
    ["essay/essay-material/index", ["taskVersionId!"]],
    ["essay/essay-compose/index", ["taskVersionId!", "planTaskId!"]],
    ["essay/essay-review/index", ["taskVersionId!"]],
    ["essay/essay-rewrite/index", ["taskVersionId!"]],
    ["essay/essay-evidence/index", ["taskVersionId!"]],
    ["pages/private-knowledge/detail/index", ["id!"]],
    ["reports/error-pattern/index", ["patternId!"]],
    ["community/community-structure/index", ["cardId!"]],
    ["reports/ability-module/index", ["track!", "moduleKey!"]],
    ["xingce/answer-ingestion/index", ["packetVersionId?|ingestionId?"]],
    ["xingce/learning-packets/index", ["contentVersionId?"]],
    ["pages/capture-import/index", ["importId?"]],
  ]);
  const values = explicit.get(route) ?? [...observedParams].filter(Boolean).map((key) => `${key}?`);
  return values.join("|");
}

function loginRequirement(route, controller) {
  if (route === "pages/login/index") return "no";
  if (route.startsWith("packages/")) return "no-asset-preview";
  if (
    /\b(?:requireAuthenticatedSession|currentAuthenticatedSession|accessToken|authSession)\b/u.test(
      controller,
    )
  ) {
    return "yes-code-evidence";
  }
  return "likely;runtime-review-required";
}

function pageTitle(route, config, template) {
  if (typeof config.navigationBarTitleText === "string" && config.navigationBarTitleText.trim()) {
    return config.navigationBarTitleText.trim();
  }
  for (const pattern of [
    /<workbench-nav\b[^>]*\btitle="([^"]+)"/u,
    /<task-shell\b[^>]*\btitle="([^"]+)"/u,
    /class="[^"]*(?:page-title|screen-title|title)[^"]*"[^>]*>\s*([^<{]+?)\s*</u,
  ]) {
    const match = template.match(pattern)?.[1]?.trim();
    if (match && !match.includes("{{")) return match;
  }
  return route
    .replace(/\/index$/u, "")
    .split("/")
    .slice(-2)
    .join("/");
}

function templateType(route, template) {
  if (route.startsWith("packages/")) return "asset-preview";
  if (template.includes("<task-shell")) return "task-shell";
  if (template.includes("<workbench-nav")) return "workbench";
  if (tabRoutes.has(route)) return "root-tab";
  if (template.includes("<safe-top")) return "safe-top-custom";
  return "page-custom";
}

function functionalDomain(route) {
  const value = route.toLowerCase();
  if (value.startsWith("packages/anan-")) return "岸岸素材";
  if (value.includes("onboarding") || value.includes("qualification")) return "建档与资格";
  if (value.includes("position")) return "岗位";
  if (
    value.includes("practice") ||
    value.includes("xingce") ||
    value.includes("reflection") ||
    value.includes("error-pattern")
  ) {
    return "刷题与错题";
  }
  if (value.includes("mock")) return "模考";
  if (value.includes("essay") || value.includes("community")) return "申论与 AI 批改";
  if (
    value.includes("today") ||
    value.includes("plan") ||
    value.includes("settlement") ||
    value.includes("adjustment")
  ) {
    return "计划与今日";
  }
  if (value.includes("report") || value.includes("ability")) return "能力与报告";
  if (
    value.includes("capture") ||
    value.includes("knowledge") ||
    value.includes("packet") ||
    value.includes("ingestion")
  ) {
    return "外部学习与资料";
  }
  if (value.includes("anan") || value.includes("notification")) return "岸岸与督学";
  if (
    value.includes("me") ||
    value.includes("profile") ||
    value.includes("membership") ||
    value.includes("privacy") ||
    value.includes("support")
  ) {
    return "账户与设置";
  }
  if (value.includes("login")) return "启动与登录";
  return "其他";
}

function resolveComponentReference(consumerJsonPath, declaredPath) {
  if (declaredPath.startsWith("/")) {
    return path.resolve(miniprogramRoot, `.${declaredPath}`);
  }
  return path.resolve(path.dirname(consumerJsonPath), declaredPath);
}

function readJson(file) {
  return JSON.parse(readText(file));
}

function safeReadJson(file) {
  try {
    return readJson(file);
  } catch {
    return null;
  }
}

function readText(file) {
  return readFileSync(file, "utf8");
}

function walkFiles(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory).flatMap((entry) => {
    const target = path.resolve(directory, entry);
    return statSync(target).isDirectory() ? walkFiles(target) : [target];
  });
}

function groupBy(values, keyFn) {
  const result = new Map();
  for (const value of values) {
    const key = keyFn(value);
    const bucket = result.get(key) ?? [];
    bucket.push(value);
    result.set(key, bucket);
  }
  return result;
}

function writeCsv(file, rows) {
  if (rows.length === 0) {
    writeFileSync(file, "");
    return;
  }
  const columns = Object.keys(rows[0]);
  const lines = [
    columns.map(csvCell).join(","),
    ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(",")),
  ];
  writeFileSync(file, `${lines.join("\n")}\n`);
}

function csvCell(value) {
  const normalized = String(value ?? "");
  return `"${normalized.replaceAll('"', '""')}"`;
}

function relativeRepositoryPath(file) {
  return path.relative(repositoryRoot, file).split(path.sep).join("/");
}

function relativeMiniprogramPath(file) {
  return path.relative(miniprogramRoot, file).split(path.sep).join("/");
}

function runGitCommit() {
  const headPath = path.resolve(repositoryRoot, ".git/HEAD");
  if (!existsSync(headPath)) return "unknown";
  const head = readText(headPath).trim();
  if (!head.startsWith("ref: ")) return head;
  const refPath = path.resolve(repositoryRoot, ".git", head.slice(5));
  return existsSync(refPath) ? readText(refPath).trim() : "unknown";
}
