import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [outputArgument, sourceCommit, sourceRepositoryArgument] = process.argv.slice(2);
assert.ok(
  outputArgument && sourceRepositoryArgument,
  "Usage: validate-final-real-render.mjs OUTPUT_ROOT SOURCE_COMMIT SOURCE_REPOSITORY",
);
assert.match(sourceCommit ?? "", /^[0-9a-f]{40}$/u, "A full source commit is required");

const repositoryRoot = path.resolve(import.meta.dirname, "../../..");
const outputRoot = path.resolve(outputArgument);
const sourceRepository = path.resolve(sourceRepositoryArgument);
const initialSourceGitEvidence = currentSourceGitEvidence(sourceRepository);
assert.equal(
  initialSourceGitEvidence.head,
  sourceCommit,
  "Current final source HEAD differs from the declared capture source",
);
assert.equal(
  initialSourceGitEvidence.status,
  "",
  "Current final source worktree is not clean",
);
const require = createRequire(import.meta.url);
const { PNG } = require(
  path.resolve(sourceRepository, "apps/wechat/node_modules/pngjs"),
);
const [{ visualScenarios }, { visualDeliveryContracts }] = await Promise.all([
  import(
    pathToFileURL(path.resolve(sourceRepository, "apps/wechat/e2e/visual-scenarios.mjs")).href
  ),
  import(
    pathToFileURL(
      path.resolve(sourceRepository, "apps/wechat/e2e/visual-delivery-contracts.mjs"),
    ).href
  ),
]);
assert.equal(visualScenarios.length, 95, "Final source visual scenario authority");
assert.equal(visualDeliveryContracts.length, 54, "Final source semantic contract authority");
assert.equal(
  visualDeliveryContracts.filter((contract) => contract.primarySelector).length,
  26,
  "Final source primary-selector authority",
);
const assetScenarios = [
  "thinking",
  "reminder",
  "encouragement",
  "rest",
  "deprioritize",
  "growth",
].map((kind) => ({
  id: `z-anan-${kind}-asset-entry`,
  route: `/packages/anan-${kind}/asset-entry/index`,
  authenticated: false,
  source: "audit-supplement",
}));
const expectedScenarios = [
  ...visualScenarios.map((scenario) => ({
    id: scenario.id,
    route: scenario.route,
    authenticated: scenario.authenticated === true,
    source: "existing-visual-scenario",
  })),
  ...assetScenarios,
];
assert.equal(expectedScenarios.length, 101, "Final combined scenario authority");
assert.equal(
  new Set(expectedScenarios.map((scenario) => scenario.id)).size,
  101,
  "Final scenario IDs must be unique",
);
const deliveryByScenario = new Map(
  visualDeliveryContracts.map((contract) => [contract.scenarioId, contract]),
);
const profiles = [
  {
    name: "small-320x568",
    width: 320,
    height: 568,
    baseWidth: 320,
    baseHeight: 568,
    orientation: "portrait",
    model: "iPhone 5",
  },
  {
    name: "standard-390x844",
    artifactName: "standard-390x844-retry-01",
    width: 390,
    height: 844,
    baseWidth: 390,
    baseHeight: 844,
    orientation: "portrait",
    model: "iPhone 12/13 (Pro)",
  },
  {
    name: "long-android-363x817",
    width: 363,
    height: 817,
    baseWidth: 363,
    baseHeight: 817,
    orientation: "portrait",
    model: "HUAWEI nova 14 Ultra",
  },
  {
    name: "ipad-portrait-768x1024",
    width: 768,
    height: 1024,
    baseWidth: 768,
    baseHeight: 1024,
    orientation: "portrait",
    model: "iPad",
  },
  {
    name: "ipad-landscape-1024x768",
    width: 1024,
    height: 768,
    baseWidth: 768,
    baseHeight: 1024,
    orientation: "landscape",
    model: "iPad",
  },
];

const allowedAuditRoot = path.resolve(repositoryRoot, "audit");
assert.ok(
  isStrictDescendant(allowedAuditRoot, outputRoot),
  "Output root must be a strict lexical descendant of audit/",
);
await assertRegularDirectory(outputRoot, "Output root");
const outputRealRoot = await realpath(outputRoot);
const allowedAuditRealRoot = await realpath(allowedAuditRoot);
assert.ok(
  isStrictDescendant(allowedAuditRealRoot, outputRealRoot),
  "Output root realpath escaped audit/",
);
const logsRoot = path.resolve(outputRoot, "logs");
const screenshotsRoot = path.resolve(outputRoot, "screenshots");
await assertRegularDirectory(logsRoot, "Logs root");
await assertRegularDirectory(screenshotsRoot, "Screenshots root");
let sharedRuntimeNonce = null;
let sharedAutomationEndpoint = null;
let sharedRuntimeProjectRealPath = null;
let sharedRuntimeTreeEvidence = null;
let sharedRuntimeIdentity = null;
const summaries = [];
for (const expected of profiles) {
  const artifactName = expected.artifactName ?? expected.name;
  const manifestPath = path.resolve(outputRoot, "logs", `${artifactName}-manifest.json`);
  await assertRegularFile(manifestPath, `${expected.name}: manifest`);
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.equal(manifest.schemaVersion, 2, `${expected.name}: schemaVersion`);
  assert.equal(manifest.auditKind, "wechat-devtools-real-render", `${expected.name}: auditKind`);
  assert.equal(manifest.profile, artifactName, `${expected.name}: artifact profile`);
  assert.equal(
    path.resolve(manifest.sourceRepository),
    sourceRepository,
    `${expected.name}: source repository`,
  );
  assert.equal(manifest.sourceCommit, sourceCommit, `${expected.name}: sourceCommit`);
  assert.equal(manifest.runtimeSourceMarker, sourceCommit, `${expected.name}: runtime marker`);
  const expectedSourceGitEvidence = {
    head: sourceCommit,
    declaredCommit: sourceCommit,
    clean: true,
    status: "",
  };
  assert.deepEqual(
    manifest.sourceGitEvidence,
    expectedSourceGitEvidence,
    `${expected.name}: source git evidence`,
  );
  assert.deepEqual(
    manifest.sourceIntegrity,
    {
      validation: "unchanged-clean-head",
      initial: expectedSourceGitEvidence,
      final: expectedSourceGitEvidence,
    },
    `${expected.name}: source integrity evidence`,
  );
  assert.equal(
    manifest.runtimeIdentity?.validation,
    "exact-disk-to-running-match",
    `${expected.name}: runtime identity`,
  );
  assert.equal(
    manifest.runtimeIdentity?.sourceCommit,
    sourceCommit,
    `${expected.name}: runtime source`,
  );
  assert.equal(manifest.runtimeIdentity?.schemaVersion, 1, `${expected.name}: runtime schema`);
  assert.match(
    manifest.runtimeIdentity?.nonce ?? "",
    /^[0-9a-f]{64}$/u,
    `${expected.name}: runtime nonce`,
  );
  if (sharedRuntimeNonce === null) sharedRuntimeNonce = manifest.runtimeIdentity.nonce;
  assert.equal(
    manifest.runtimeIdentity.nonce,
    sharedRuntimeNonce,
    `${expected.name}: runtime nonce drifted across profiles`,
  );
  const runtimeProject = path.resolve(repositoryRoot, manifest.projectSource ?? "");
  await assertRegularDirectory(runtimeProject, `${expected.name}: runtime project`);
  const runtimeProjectRealPath = await realpath(runtimeProject);
  assert.ok(
    isStrictDescendant(allowedAuditRealRoot, runtimeProjectRealPath),
    `${expected.name}: runtime project escaped audit root`,
  );
  assert.equal(
    path.dirname(runtimeProjectRealPath),
    path.resolve(allowedAuditRealRoot, "repair-evidence"),
    `${expected.name}: runtime project parent`,
  );
  assert.match(
    path.basename(runtimeProjectRealPath),
    /^runtime-project-.+/u,
    `${expected.name}: runtime project name`,
  );
  assert.equal(
    pathsAreDisjoint(runtimeProjectRealPath, outputRealRoot),
    true,
    `${expected.name}: runtime and output trees overlap`,
  );
  const runtimeSourceMarkerPath = path.resolve(runtimeProject, ".audit-source-commit");
  const runtimeIdentityPath = path.resolve(runtimeProject, ".audit-runtime-identity.json");
  await assertRegularFile(runtimeSourceMarkerPath, `${expected.name}: runtime source marker`);
  await assertRegularFile(runtimeIdentityPath, `${expected.name}: runtime identity`);
  assert.equal(
    (await readFile(runtimeSourceMarkerPath, "utf8")).trim(),
    sourceCommit,
    `${expected.name}: runtime project source marker`,
  );
  const diskRuntimeIdentity = JSON.parse(
    await readFile(runtimeIdentityPath, "utf8"),
  );
  assert.equal(diskRuntimeIdentity.schemaVersion, 1, `${expected.name}: disk runtime schema`);
  assert.equal(
    diskRuntimeIdentity.nonce,
    sharedRuntimeNonce,
    `${expected.name}: disk runtime nonce`,
  );
  assert.equal(
    diskRuntimeIdentity.sourceCommit,
    sourceCommit,
    `${expected.name}: disk runtime source`,
  );
  assert.match(
    diskRuntimeIdentity.prepareToolCommit ?? "",
    /^[0-9a-f]{40}$/u,
    `${expected.name}: prepare tool commit`,
  );
  assert.match(
    diskRuntimeIdentity.prepareToolBlob ?? "",
    /^[0-9a-f]{40}$/u,
    `${expected.name}: prepare tool blob`,
  );
  const prepareToolPath = "tools/audit/c/prepare_runtime_project.sh";
  assert.equal(
    execFileSync(
      "git",
      ["rev-parse", "--verify", `${diskRuntimeIdentity.prepareToolCommit}^{commit}`],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
      },
    ).trim(),
    diskRuntimeIdentity.prepareToolCommit,
    `${expected.name}: prepare tool commit is unavailable`,
  );
  assert.equal(
    diskRuntimeIdentity.prepareToolBlob,
    execFileSync(
      "git",
      ["rev-parse", `${diskRuntimeIdentity.prepareToolCommit}:${prepareToolPath}`],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
      },
    ).trim(),
    `${expected.name}: prepare tool blob/commit mismatch`,
  );
  assert.equal(
    diskRuntimeIdentity.prepareToolBlob,
    execFileSync("git", ["rev-parse", `${sourceCommit}:${prepareToolPath}`], {
      cwd: sourceRepository,
      encoding: "utf8",
    }).trim(),
    `${expected.name}: prepare tool does not match final source`,
  );
  if (sharedRuntimeProjectRealPath === null) {
    sharedRuntimeProjectRealPath = runtimeProjectRealPath;
    sharedRuntimeIdentity = diskRuntimeIdentity;
    sharedRuntimeTreeEvidence = await validateRuntimeTree({
      sourceRepository,
      runtimeProject,
      runtimeIdentity: diskRuntimeIdentity,
    });
  } else {
    assert.equal(
      runtimeProjectRealPath,
      sharedRuntimeProjectRealPath,
      `${expected.name}: runtime project drifted across profiles`,
    );
  }
  assert.ok(
    ["connected-loopback", "launched-prepared-runtime"].includes(
      manifest.automationBinding?.mode,
    ),
    `${expected.name}: automation binding`,
  );
  assert.equal(
    manifest.automationBinding?.runtimeIdentityValidation,
    "exact-disk-to-running-match",
    `${expected.name}: automation identity`,
  );
  assert.equal(
    manifest.devtoolsCli,
    "/Applications/wechatwebdevtools.app/Contents/MacOS/cli",
    `${expected.name}: DevTools CLI`,
  );
  if (manifest.automationBinding.mode === "connected-loopback") {
    const automationEndpoint = new URL(manifest.automationBinding?.endpoint ?? "");
    assert.equal(automationEndpoint.protocol, "ws:", `${expected.name}: automation protocol`);
    assert.equal(automationEndpoint.hostname, "127.0.0.1", `${expected.name}: automation host`);
    assert.match(automationEndpoint.port, /^\d+$/u, `${expected.name}: automation port`);
    assert.equal(automationEndpoint.pathname, "/", `${expected.name}: automation path`);
    assert.equal(automationEndpoint.search, "", `${expected.name}: automation query`);
    assert.equal(automationEndpoint.hash, "", `${expected.name}: automation fragment`);
    if (sharedAutomationEndpoint === null) {
      sharedAutomationEndpoint = manifest.automationBinding.endpoint;
    }
    assert.equal(
      manifest.automationBinding.endpoint,
      sharedAutomationEndpoint,
      `${expected.name}: automation endpoint drifted across connected profiles`,
    );
  } else {
    assert.equal(
      manifest.automationBinding.endpoint,
      null,
      `${expected.name}: launched runtime must not declare a remote endpoint`,
    );
  }
  assert.equal(manifest.systemInfo?.model, expected.model, `${expected.name}: model`);
  assert.equal(
    manifest.systemInfo?.windowWidth,
    expected.baseWidth,
    `${expected.name}: base window width`,
  );
  assert.equal(
    manifest.systemInfo?.windowHeight,
    expected.baseHeight,
    `${expected.name}: base window height`,
  );
  assert.equal(
    manifest.systemInfo?.screenWidth,
    expected.baseWidth,
    `${expected.name}: base screen width`,
  );
  assert.equal(
    manifest.systemInfo?.screenHeight,
    expected.baseHeight,
    `${expected.name}: base screen height`,
  );
  assert.equal(
    manifest.systemInfo?.deviceOrientation,
    expected.orientation,
    `${expected.name}: orientation`,
  );
  assert.deepEqual(
    manifest.expectedViewport,
    {
      width: expected.width,
      height: expected.height,
      orientation: expected.orientation,
    },
    `${expected.name}: expected viewport`,
  );
  assert.deepEqual(
    manifest.guards?.allowedHttpUrls,
    ["http://127.0.0.1:9"],
    `${expected.name}: local-only URL`,
  );
  assert.equal(
    manifest.guards?.startupRuntimeConfiguration,
    "local-only",
    `${expected.name}: startup runtime configuration`,
  );
  assert.equal(
    manifest.guards?.evidenceBoundary,
    "Local-only runtime plus installed platform mocks; this is prevention evidence, not packet capture.",
    `${expected.name}: network evidence boundary`,
  );
  assert.deepEqual(
    manifest.guards?.installation,
    {
      wxRequest: "mocked-503-and-probed",
      wxUploadFile: "mock-installed",
      wxDownloadFile: "mock-installed",
      wxRequestPayment: "mock-installed",
      wxRequestVirtualPayment: "mock-installed",
      requestProbe: {
        callback: "success",
        statusCode: 503,
        code: "ui_audit_network_blocked",
      },
    },
    `${expected.name}: network/payment guards`,
  );
  assert.equal(manifest.scenarioCount, 101, `${expected.name}: scenario count`);
  assert.equal(manifest.capturedCount, 101, `${expected.name}: captured count`);
  assert.equal(manifest.failedCount, 0, `${expected.name}: failed count`);
  assert.equal(manifest.uniqueRouteCount, 65, `${expected.name}: route count`);
  assert.equal(manifest.runtimeFailureCount, 0, `${expected.name}: runtime failures`);
  assert.equal(manifest.actionableWarningCount, 0, `${expected.name}: warnings`);
  assert.equal(manifest.fatalError, null, `${expected.name}: fatal error`);
  assert.equal(manifest.records.length, 101, `${expected.name}: records`);
  assert.equal(
    new Set(manifest.records.map((record) => routePath(record.route))).size,
    manifest.uniqueRouteCount,
    `${expected.name}: unique route count drift`,
  );
  const profileLogPath = path.resolve(logsRoot, `${artifactName}.jsonl`);
  await assertRegularFile(profileLogPath, `${expected.name}: JSONL log`);
  const loggedRecords = (await readFile(profileLogPath, "utf8"))
    .trimEnd()
    .split("\n")
    .map((line, index) => {
      assert.notEqual(line, "", `${expected.name}: empty JSONL line ${index + 1}`);
      const loggedEnvelope = JSON.parse(line);
      assert.equal(
        loggedEnvelope.type,
        "scenario",
        `${expected.name}: JSONL envelope type ${index + 1}`,
      );
      const { type: _type, ...loggedRecord } = loggedEnvelope;
      return loggedRecord;
    });
  assert.equal(loggedRecords.length, 101, `${expected.name}: JSONL record count`);
  assert.deepEqual(loggedRecords, manifest.records, `${expected.name}: JSONL/manifest record drift`);

  const scenarioIds = manifest.records.map((record) => record.id);
  assert.equal(new Set(scenarioIds).size, 101, `${expected.name}: unique scenario IDs`);
  assert.equal(
    manifest.records.filter((record) => record.source === "existing-visual-scenario").length,
    95,
    `${expected.name}: business scenarios`,
  );
  assert.equal(
    manifest.records.filter((record) => record.source === "audit-supplement").length,
    6,
    `${expected.name}: asset scenarios`,
  );

  const horizontalOverflows = [];
  const primaryVerticalOverflows = [];
  const touchTargetsBelow44 = [];
  const viewportHorizontalOverflows = [];
  const screenshotPaths = new Set();
  const screenshotHashes = [];
  for (const [recordIndex, record] of manifest.records.entries()) {
    const expectedScenario = expectedScenarios[recordIndex];
    assert.equal(record.id, expectedScenario.id, `${expected.name}: scenario order/id`);
    assert.equal(record.route, expectedScenario.route, `${expected.name}/${record.id}: route`);
    assert.equal(
      record.routePath,
      routePath(expectedScenario.route),
      `${expected.name}/${record.id}: routePath`,
    );
    assert.equal(
      record.authenticated,
      expectedScenario.authenticated,
      `${expected.name}/${record.id}: authentication fixture`,
    );
    assert.equal(record.source, expectedScenario.source, `${expected.name}/${record.id}: source`);
    assert.equal(record.profile, artifactName, `${expected.name}/${record.id}: record profile`);
    assert.equal(record.status, "captured", `${expected.name}/${record.id}: status`);
    assert.equal(record.stage, "completed", `${expected.name}/${record.id}: stage`);
    assert.equal(record.error, "", `${expected.name}/${record.id}: error`);
    assert.deepEqual(record.runtimeFailures, [], `${expected.name}/${record.id}: runtime failures`);
    assert.deepEqual(
      record.actionableWarnings,
      [],
      `${expected.name}/${record.id}: actionable warnings`,
    );
    assert.equal(
      record.activeRoute,
      record.routePath.replace(/^\//u, ""),
      `${expected.name}/${record.id}: active route`,
    );
    assert.equal(
      record.routeSystemInfo?.model,
      expected.model,
      `${expected.name}/${record.id}: route model`,
    );
    assert.equal(
      record.routeSystemInfo?.windowWidth,
      expected.baseWidth,
      `${expected.name}/${record.id}: route base width`,
    );
    assert.equal(
      record.routeSystemInfo?.windowHeight,
      expected.baseHeight,
      `${expected.name}/${record.id}: route base height`,
    );
    assert.equal(
      record.routeSystemInfo?.screenWidth,
      expected.baseWidth,
      `${expected.name}/${record.id}: route base screen width`,
    );
    assert.equal(
      record.routeSystemInfo?.screenHeight,
      expected.baseHeight,
      `${expected.name}/${record.id}: route base screen height`,
    );
    assert.equal(
      record.routeSystemInfo?.deviceOrientation,
      expected.orientation,
      `${expected.name}/${record.id}: route orientation`,
    );
    assert.equal(
      record.screenshotWidth,
      expected.width,
      `${expected.name}/${record.id}: screenshot width`,
    );
    assert.ok(
      Number.isInteger(record.screenshotHeight) &&
        record.screenshotHeight <= expected.height &&
        record.screenshotHeight >= expected.height - 120,
      `${expected.name}/${record.id}: screenshot height/native chrome inset`,
    );
    assert.equal(
      record.screenshotNativeChromeInset,
      expected.height - record.screenshotHeight,
      `${expected.name}/${record.id}: native chrome inset`,
    );
    assert.ok(record.viewport && typeof record.viewport === "object", `${expected.name}/${record.id}: viewport`);
    for (const property of [
      "scrollLeft",
      "scrollTop",
      "scrollWidth",
      "scrollHeight",
    ]) {
      assert.equal(
        Number.isFinite(record.viewport[property]),
        true,
        `${expected.name}/${record.id}: viewport ${property}`,
      );
    }
    assert.ok(
      record.viewport.scrollWidth > 0,
      `${expected.name}/${record.id}: viewport scrollWidth`,
    );
    assert.ok(
      record.viewport.scrollHeight > 0,
      `${expected.name}/${record.id}: viewport scrollHeight`,
    );
    const screenshotPath = path.resolve(repositoryRoot, record.screenshot);
    assert.equal(
      screenshotPath,
      path.resolve(outputRoot, "screenshots", artifactName, `${record.id}.png`),
      `${expected.name}/${record.id}: canonical screenshot path`,
    );
    assert.ok(
      isStrictDescendant(outputRoot, screenshotPath),
      `${expected.name}/${record.id}: screenshot escaped output root`,
    );
    assert.ok(
      isStrictDescendant(outputRealRoot, await realpath(screenshotPath)),
      `${expected.name}/${record.id}: screenshot realpath escaped output root`,
    );
    const screenshotStat = await lstat(screenshotPath);
    assert.equal(
      screenshotStat.isSymbolicLink(),
      false,
      `${expected.name}/${record.id}: screenshot is a symlink`,
    );
    assert.equal(
      screenshotStat.isFile(),
      true,
      `${expected.name}/${record.id}: screenshot is not a regular file`,
    );
    assert.equal(
      screenshotPaths.has(screenshotPath),
      false,
      `${expected.name}/${record.id}: duplicate screenshot reference`,
    );
    screenshotPaths.add(screenshotPath);
    const screenshotBytes = await readFile(screenshotPath);
    assert.deepEqual(
      pngDimensions(screenshotBytes, screenshotPath),
      { width: expected.width, height: record.screenshotHeight },
      `${expected.name}/${record.id}: PNG dimensions`,
    );
    const decodedPng = PNG.sync.read(screenshotBytes, { checkCRC: true });
    assert.deepEqual(
      { width: decodedPng.width, height: decodedPng.height },
      { width: expected.width, height: record.screenshotHeight },
      `${expected.name}/${record.id}: fully decoded PNG dimensions`,
    );
    screenshotHashes.push({
      id: record.id,
      sha256: createHash("sha256").update(screenshotBytes).digest("hex"),
    });
    if (Number(record.viewport?.scrollWidth) > expected.width + 1) {
      viewportHorizontalOverflows.push({
        id: record.id,
        scrollWidth: record.viewport.scrollWidth,
        viewportWidth: expected.width,
      });
    }
    const deliveryContract = deliveryByScenario.get(record.id);
    if (!deliveryContract) {
      assert.equal(record.semantic, null, `${expected.name}/${record.id}: unexpected semantic data`);
    } else {
      assert.ok(record.semantic, `${expected.name}/${record.id}: missing semantic contract`);
      assert.equal(
        record.semantic.figmaNodeId,
        deliveryContract.figmaNodeId,
        `${expected.name}/${record.id}: Figma node`,
      );
      assert.equal(
        record.semantic.routeState,
        deliveryContract.routeState ?? null,
        `${expected.name}/${record.id}: route state`,
      );
      assert.equal(
        record.semantic.semanticSelector,
        deliveryContract.semanticSelector,
        `${expected.name}/${record.id}: semantic selector authority`,
      );
      assert.equal(
        record.semantic.primarySelector,
        deliveryContract.primarySelector ?? null,
        `${expected.name}/${record.id}: primary selector authority`,
      );
      assertRectangle(
        record.semantic.semanticGeometry,
        `${expected.name}/${record.id}: semantic selector`,
      );
      const semanticOverflow = hasHorizontalOverflow(
        record.semantic.semanticGeometry,
        expected.width,
      );
      assert.equal(
        record.semantic.semanticHorizontalOverflow,
        semanticOverflow,
        `${expected.name}/${record.id}: semantic overflow calculation`,
      );
      if (semanticOverflow) {
        horizontalOverflows.push({
          id: record.id,
          selector: record.semantic.semanticSelector,
          kind: "semantic",
          geometry: record.semantic.semanticGeometry,
        });
      }
      if (record.semantic.primarySelector !== null) {
        assertRectangle(
          record.semantic.primaryGeometry,
          `${expected.name}/${record.id}: primary selector`,
        );
        const primaryOverflow = hasHorizontalOverflow(
          record.semantic.primaryGeometry,
          expected.width,
        );
        const primaryVerticalOverflow = hasVerticalOverflow(
          record.semantic.primaryGeometry,
          expected.height,
        );
        const primaryTouchHeightPass = Number(record.semantic.primaryGeometry.height) >= 44;
        assert.equal(
          record.semantic.primaryHorizontalOverflow,
          primaryOverflow,
          `${expected.name}/${record.id}: primary overflow calculation`,
        );
        assert.equal(
          record.semantic.primaryTouchHeightPass,
          primaryTouchHeightPass,
          `${expected.name}/${record.id}: primary touch calculation`,
        );
        if (primaryOverflow) {
          horizontalOverflows.push({
            id: record.id,
            selector: record.semantic.primarySelector,
            kind: "primary",
            geometry: record.semantic.primaryGeometry,
          });
        }
        if (primaryVerticalOverflow) {
          primaryVerticalOverflows.push({
            id: record.id,
            selector: record.semantic.primarySelector,
            geometry: record.semantic.primaryGeometry,
            viewportHeight: expected.height,
          });
        }
        if (!primaryTouchHeightPass) {
          touchTargetsBelow44.push({
            id: record.id,
            selector: record.semantic.primarySelector,
            height: record.semantic.primaryGeometry.height,
          });
        }
      } else {
        assert.equal(
          record.semantic.primaryGeometry,
          null,
          `${expected.name}/${record.id}: unexpected primary geometry`,
        );
        assert.equal(
          record.semantic.primaryTouchHeightPass,
          null,
          `${expected.name}/${record.id}: unexpected primary touch result`,
        );
      }
    }
    validateSupplementalLayoutEvidence(record, expected);
  }
  const semanticRecords = manifest.records.filter((record) => record.semantic !== null);
  assert.equal(semanticRecords.length, 54, `${expected.name}: semantic contract count`);

  assert.equal(
    path.resolve(manifest.captureTool?.repository ?? ""),
    repositoryRoot,
    `${expected.name}: capture tool repository`,
  );
  assert.match(
    manifest.captureTool?.commit ?? "",
    /^[0-9a-f]{40}$/u,
    `${expected.name}: capture tool commit`,
  );
  assert.equal(
    execFileSync(
      "git",
      ["rev-parse", "--verify", `${manifest.captureTool.commit}^{commit}`],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
      },
    ).trim(),
    manifest.captureTool.commit,
    `${expected.name}: capture tool commit is unavailable`,
  );
  for (const file of manifest.captureTool?.files ?? []) {
    assert.equal(file.clean, true, `${expected.name}: dirty capture tool ${file.path}`);
    assert.equal(
      file.treeBlob,
      file.workingBlob,
      `${expected.name}: capture tool drift ${file.path}`,
    );
    assert.equal(
      file.treeBlob,
      execFileSync(
        "git",
        ["rev-parse", `${manifest.captureTool.commit}:${file.path}`],
        {
          cwd: repositoryRoot,
          encoding: "utf8",
        },
      ).trim(),
      `${expected.name}: capture tool blob/commit mismatch ${file.path}`,
    );
    assert.equal(
      file.treeBlob,
      execFileSync("git", ["rev-parse", `${sourceCommit}:${file.path}`], {
        cwd: sourceRepository,
        encoding: "utf8",
      }).trim(),
      `${expected.name}: capture tool does not match final source ${file.path}`,
    );
  }
  assert.equal(manifest.captureTool?.files?.length, 2, `${expected.name}: capture tool files`);
  assert.deepEqual(
    manifest.captureTool.files.map((file) => file.path).sort(),
    ["tools/audit/c/capture-real-ui.mjs", "tools/audit/c/capture-safety.mjs"],
    `${expected.name}: capture tool path authority`,
  );

  const profileScreenshotDirectory = path.resolve(screenshotsRoot, artifactName);
  await assertRegularDirectory(
    profileScreenshotDirectory,
    `${expected.name}: screenshot directory`,
  );
  const screenshotFiles = (await readdir(profileScreenshotDirectory)).filter((entry) =>
    entry.endsWith(".png"),
  );
  assert.equal(screenshotFiles.length, 101, `${expected.name}: PNG file count`);
  assert.deepEqual(
    screenshotFiles.sort(),
    expectedScenarios.map((scenario) => `${scenario.id}.png`).sort(),
    `${expected.name}: canonical PNG filename set`,
  );
  const dimensions = Object.entries(
    manifest.records.reduce((counts, record) => {
      const key = `${record.screenshotWidth}x${record.screenshotHeight}`;
      counts[key] = (counts[key] ?? 0) + 1;
      return counts;
    }, {}),
  )
    .map(([size, count]) => ({ size, count }))
    .sort((left, right) => left.size.localeCompare(right.size));
  summaries.push({
    profile: expected.name,
    artifactProfile: artifactName,
    automationMode: manifest.automationBinding.mode,
    runtimeTreeSha256: sharedRuntimeTreeEvidence.sha256,
    runtimeTreeFileCount: sharedRuntimeTreeEvidence.fileCount,
    captured: manifest.capturedCount,
    routes: manifest.uniqueRouteCount,
    businessScenarios: 95,
    assetScenarios: 6,
    semanticContracts: semanticRecords.length,
    horizontalOverflows,
    primaryVerticalOverflows,
    viewportHorizontalOverflows,
    touchTargetsBelow44,
    dimensions,
    screenshotSetSha256: createHash("sha256")
      .update(
        screenshotHashes
          .sort((left, right) => left.id.localeCompare(right.id))
          .map((item) => `${item.id}\t${item.sha256}`)
          .join("\n"),
      )
      .digest("hex"),
    failures: manifest.failedCount,
    runtimeFailures: manifest.runtimeFailureCount,
    actionableWarnings: manifest.actionableWarningCount,
  });
}

const finalRuntimeTreeEvidence = await validateRuntimeTree({
  sourceRepository,
  runtimeProject: sharedRuntimeProjectRealPath,
  runtimeIdentity: sharedRuntimeIdentity,
});
assert.deepEqual(
  finalRuntimeTreeEvidence,
  sharedRuntimeTreeEvidence,
  "Runtime tree changed during final evidence validation",
);
sharedRuntimeTreeEvidence = {
  ...sharedRuntimeTreeEvidence,
  validation: "unchanged-during-final-validation",
  contentValidation: finalRuntimeTreeEvidence.validation,
  initialSha256: sharedRuntimeTreeEvidence.sha256,
  finalSha256: finalRuntimeTreeEvidence.sha256,
};
const finalSourceGitEvidence = currentSourceGitEvidence(sourceRepository);
assert.deepEqual(
  finalSourceGitEvidence,
  initialSourceGitEvidence,
  "Final source worktree changed during final evidence validation",
);

process.stdout.write(
  `${JSON.stringify(
    {
      sourceCommit,
      sourceGitEvidence: {
        validation: "unchanged-clean-head-during-final-validation",
        initial: initialSourceGitEvidence,
        final: finalSourceGitEvidence,
      },
      structuralValidation: "pass",
      runtimeTreeEvidence: sharedRuntimeTreeEvidence,
      profiles: summaries,
      totals: {
        profiles: summaries.length,
        screenshots: summaries.reduce((sum, item) => sum + item.captured, 0),
        businessScenarioRenders: summaries.length * 95,
        assetEntryRenders: summaries.length * 6,
        pageProfileOpens: summaries.length * 59,
        uiExceptions: {
          horizontalOverflows: summaries.reduce(
            (sum, item) => sum + item.horizontalOverflows.length,
            0,
          ),
          primaryVerticalOverflows: summaries.reduce(
            (sum, item) => sum + item.primaryVerticalOverflows.length,
            0,
          ),
          viewportHorizontalOverflows: summaries.reduce(
            (sum, item) => sum + item.viewportHorizontalOverflows.length,
            0,
          ),
          touchTargetsBelow44: summaries.reduce(
            (sum, item) => sum + item.touchTargetsBelow44.length,
            0,
          ),
        },
      },
    },
    null,
    2,
  )}\n`,
);

function isStrictDescendant(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== "..";
}

function pathsAreDisjoint(first, second) {
  return (
    first !== second &&
    !isStrictDescendant(first, second) &&
    !isStrictDescendant(second, first)
  );
}

function currentSourceGitEvidence(repository) {
  return {
    head: execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
      cwd: repository,
      encoding: "utf8",
    }).trim(),
    status: execFileSync(
      "git",
      ["status", "--porcelain=v1", "--untracked-files=normal"],
      {
        cwd: repository,
        encoding: "utf8",
      },
    ),
  };
}

async function assertRegularDirectory(target, label) {
  const targetStat = await lstat(target);
  assert.equal(targetStat.isSymbolicLink(), false, `${label} is a symlink`);
  assert.equal(targetStat.isDirectory(), true, `${label} is not a directory`);
}

async function assertRegularFile(target, label) {
  const targetStat = await lstat(target);
  assert.equal(targetStat.isSymbolicLink(), false, `${label} is a symlink`);
  assert.equal(targetStat.isFile(), true, `${label} is not a regular file`);
}

async function validateRuntimeTree({
  sourceRepository,
  runtimeProject,
  runtimeIdentity,
}) {
  const sourceWechatRoot = path.resolve(sourceRepository, "apps/wechat");
  const expectedRootEntries = [
    ".audit-runtime-identity.json",
    ".audit-source-commit",
    "cloudfunctions",
    "miniprogram",
    "node_modules",
    "package.json",
    "project.config.json",
    "tsconfig.json",
  ];
  assert.deepEqual(
    (await readdir(runtimeProject)).sort(),
    expectedRootEntries,
    "Runtime root contains an unexpected or missing entry; DevTools private configuration requires explicit manual review",
  );
  const nodeModulesPath = path.resolve(runtimeProject, "node_modules");
  const nodeModulesStat = await lstat(nodeModulesPath);
  assert.equal(nodeModulesStat.isSymbolicLink(), true, "Runtime node_modules must be a symlink");
  assert.equal(
    await realpath(nodeModulesPath),
    await realpath(path.resolve(sourceWechatRoot, "node_modules")),
    "Runtime node_modules does not resolve to the final source dependency directory",
  );

  const digestRecords = [];
  for (const filename of ["package.json", "tsconfig.json"]) {
    const runtimeFilePath = path.resolve(runtimeProject, filename);
    await assertRegularFile(runtimeFilePath, `Runtime ${filename}`);
    assert.ok(
      isStrictDescendant(runtimeProject, await realpath(runtimeFilePath)),
      `Runtime ${filename} escaped runtime root`,
    );
    const sourceBytes = await readFile(path.resolve(sourceWechatRoot, filename));
    const runtimeBytes = await readFile(runtimeFilePath);
    assert.deepEqual(runtimeBytes, sourceBytes, `Runtime ${filename} drifted from final source`);
    digestRecords.push(runtimeDigestRecord(filename, runtimeBytes));
  }
  const sourceProjectConfiguration = JSON.parse(
    await readFile(path.resolve(sourceWechatRoot, "project.config.json"), "utf8"),
  );
  const runtimeProjectConfigurationPath = path.resolve(
    runtimeProject,
    "project.config.json",
  );
  await assertRegularFile(
    runtimeProjectConfigurationPath,
    "Runtime project.config.json",
  );
  assert.ok(
    isStrictDescendant(
      runtimeProject,
      await realpath(runtimeProjectConfigurationPath),
    ),
    "Runtime project.config.json escaped runtime root",
  );
  const runtimeProjectConfigurationBytes = await readFile(
    runtimeProjectConfigurationPath,
  );
  assert.deepEqual(
    JSON.parse(runtimeProjectConfigurationBytes.toString("utf8")),
    sourceProjectConfiguration,
    "Runtime project.config.json changed semantically",
  );
  digestRecords.push(
    runtimeDigestRecord("project.config.json", runtimeProjectConfigurationBytes),
  );
  for (const subtree of ["miniprogram", "cloudfunctions"]) {
    const sourceRoot = path.resolve(sourceWechatRoot, subtree);
    const runtimeRoot = path.resolve(runtimeProject, subtree);
    await assertRegularDirectory(runtimeRoot, `Runtime ${subtree}`);
    assert.ok(
      isStrictDescendant(runtimeProject, await realpath(runtimeRoot)),
      `Runtime ${subtree} escaped runtime root`,
    );
    const [sourceFiles, runtimeFiles] = await Promise.all([
      listRegularTreeFiles(sourceRoot, subtree),
      listRegularTreeFiles(runtimeRoot, subtree),
    ]);
    assert.deepEqual(
      runtimeFiles,
      sourceFiles,
      `Runtime ${subtree} file set drifted from final source`,
    );
    for (const relativePath of sourceFiles) {
      const sourceBytes = await readFile(path.resolve(sourceWechatRoot, relativePath));
      const runtimeBytes = await readFile(path.resolve(runtimeProject, relativePath));
      let expectedBytes = sourceBytes;
      if (relativePath === "miniprogram/app.ts") {
        expectedBytes = Buffer.from(
          expectedAuditAppSource(sourceBytes.toString("utf8"), runtimeIdentity),
        );
      } else if (relativePath === "miniprogram/config/environment.ts") {
        expectedBytes = Buffer.from(
          expectedAuditEnvironmentSource(sourceBytes.toString("utf8")),
        );
      }
      assert.deepEqual(
        runtimeBytes,
        expectedBytes,
        `Runtime file drifted outside allowed transformations: ${relativePath}`,
      );
      digestRecords.push(runtimeDigestRecord(relativePath, runtimeBytes));
    }
  }

  digestRecords.sort((left, right) => left.path.localeCompare(right.path));
  const sourceMarkerPath = path.resolve(runtimeProject, ".audit-source-commit");
  const runtimeIdentityPath = path.resolve(
    runtimeProject,
    ".audit-runtime-identity.json",
  );
  for (const [metadataPath, label] of [
    [sourceMarkerPath, "Runtime source marker metadata"],
    [runtimeIdentityPath, "Runtime identity metadata"],
  ]) {
    await assertRegularFile(metadataPath, label);
    assert.ok(
      isStrictDescendant(runtimeProject, await realpath(metadataPath)),
      `${label} escaped runtime root`,
    );
  }
  const sourceMarkerBytes = await readFile(sourceMarkerPath);
  assert.equal(
    sourceMarkerBytes.toString("utf8").trim(),
    runtimeIdentity.sourceCommit,
    "Runtime source marker changed",
  );
  const runtimeIdentityBytes = await readFile(runtimeIdentityPath);
  assert.deepEqual(
    JSON.parse(runtimeIdentityBytes.toString("utf8")),
    runtimeIdentity,
    "Runtime identity metadata changed",
  );
  digestRecords.push(
    runtimeDigestRecord(".audit-source-commit", sourceMarkerBytes),
    runtimeDigestRecord(".audit-runtime-identity.json", runtimeIdentityBytes),
  );
  digestRecords.sort((left, right) => left.path.localeCompare(right.path));
  return {
    validation: "current-full-tree-matches-final-source-plus-declared-audit-transformations",
    fileCount: digestRecords.length,
    sha256: createHash("sha256")
      .update(
        digestRecords
          .map((record) => `${record.path}\u0000${record.sha256}`)
          .join("\n"),
      )
      .digest("hex"),
    allowedTransformations: [
      "miniprogram/app.ts: runtime identity injection",
      "miniprogram/config/environment.ts: local-only URL/font/support substitution",
      "project.config.json: JSON-equivalent DevTools formatting only",
      "node_modules: symlink to the final source dependency directory",
      ".audit-source-commit and .audit-runtime-identity.json: audit metadata",
    ],
    privateConfiguration: {
      present: false,
      policy: "fail closed pending explicit manual review if DevTools creates it",
    },
    dependencyBoundary:
      "node_modules target identity is checked, but ignored dependency bytes are not independently attested by Git.",
    temporalBoundary:
      "Validated against the current runtime tree after capture; legacy manifests do not contain an at-capture full-tree digest.",
  };
}

async function listRegularTreeFiles(root, label) {
  const files = [];
  async function visit(directory, prefix) {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolutePath = path.resolve(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(absolutePath, relativePath);
      } else {
        assert.equal(
          entry.isFile(),
          true,
          `Runtime/source ${label} tree contains a non-regular entry: ${relativePath}`,
        );
        files.push(`${label}/${relativePath}`);
      }
    }
  }
  await visit(root, "");
  return files.sort();
}

function expectedAuditAppSource(source, runtimeIdentity) {
  let transformed = replaceExactlyOnce(
    source,
    "export interface CoachAppState {\n",
    `export interface CoachAppState {\n  __wechatAuditRuntimeIdentity: {\n    schemaVersion: 1;\n    nonce: string;\n    sourceCommit: string;\n  };\n`,
    "CoachAppState identity interface",
  );
  transformed = replaceExactlyOnce(
    transformed,
    "  globalData: {\n",
    `  globalData: {\n    __wechatAuditRuntimeIdentity: {\n      schemaVersion: 1,\n      nonce: "${runtimeIdentity.nonce}",\n      sourceCommit: "${runtimeIdentity.sourceCommit}",\n    },\n`,
    "globalData runtime identity",
  );
  return transformed;
}

function expectedAuditEnvironmentSource(source) {
  let transformed = source;
  for (const [search, replacement, label] of [
    [
      'apiBaseUrl: "https://kg.xingguoxian.cn"',
      'apiBaseUrl: "http://127.0.0.1:9"',
      "API origin",
    ],
    [
      'mapleMonoCnRegularUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-regular-v7.9.woff"',
      "mapleMonoCnRegularUrl: null",
      "regular font URL",
    ],
    [
      'mapleMonoCnBoldUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-bold-v7.9.woff"',
      "mapleMonoCnBoldUrl: null",
      "bold font URL",
    ],
    [
      'ownedHttpsOrigins: ["https://kg.xingguoxian.cn"]',
      "ownedHttpsOrigins: []",
      "owned HTTPS origins",
    ],
  ]) {
    transformed = replaceExactlyOnce(transformed, search, replacement, label);
  }
  return transformed.replace(
    /supportGroupQrImageUrl: "[^"]*"/gu,
    "supportGroupQrImageUrl: null",
  );
}

function replaceExactlyOnce(source, search, replacement, label) {
  assert.equal(
    source.split(search).length - 1,
    1,
    `Expected exactly one ${label} transformation anchor`,
  );
  return source.replace(search, replacement);
}

function runtimeDigestRecord(relativePath, bytes) {
  return {
    path: relativePath,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

function routePath(route) {
  return String(route).split("?")[0];
}

function hasHorizontalOverflow(geometry, viewportWidth) {
  return (
    Number(geometry.left) < -1 ||
    Number(geometry.left) + Number(geometry.width) > Number(viewportWidth) + 1
  );
}

function hasVerticalOverflow(geometry, viewportHeight) {
  return (
    Number(geometry.top) < -1 ||
    Number(geometry.top) + Number(geometry.height) > Number(viewportHeight) + 1
  );
}

function assertRectangle(geometry, label) {
  assert.ok(geometry && typeof geometry === "object", `${label}: missing geometry`);
  for (const property of ["left", "top", "width", "height"]) {
    assert.equal(
      Number.isFinite(geometry[property]),
      true,
      `${label}: ${property} must be finite`,
    );
  }
  assert.ok(geometry.width > 0, `${label}: width must be positive`);
  assert.ok(geometry.height > 0, `${label}: height must be positive`);
}

function validateSupplementalLayoutEvidence(record, profile) {
  const label = `${profile.name}/${record.id}: supplemental layout evidence`;
  if (record.id === "h17-capture-camera-init-error") {
    const evidence = record.layoutEvidence;
    assert.ok(evidence && typeof evidence === "object", `${label}: missing`);
    assert.equal(evidence.kind, "capture-error-recovery-layout", `${label}: kind`);
    assert.deepEqual(
      Object.keys(evidence.selectors ?? {}).sort(),
      ["alternateEntry", "controls", "error", "privacy", "shutter"],
      `${label}: selector set`,
    );
    for (const [name, rectangle] of Object.entries(evidence.selectors)) {
      assertRectangle(rectangle, `${label}/${name}`);
      assert.equal(
        evidence.withinViewport?.[name],
        rectangleWithinViewport(rectangle, profile.width, profile.height),
        `${label}/${name}: viewport calculation`,
      );
      assert.equal(evidence.withinViewport[name], true, `${label}/${name}: outside viewport`);
    }
    assert.deepEqual(
      Object.keys(evidence.withinViewport ?? {}).sort(),
      ["alternateEntry", "controls", "error", "privacy", "shutter"],
      `${label}: viewport selector set`,
    );
    assert.equal(
      evidence.overlaps?.errorWithControls,
      rectanglesOverlap(evidence.selectors.error, evidence.selectors.controls),
      `${label}: error/controls overlap calculation`,
    );
    assert.equal(
      evidence.overlaps?.errorWithShutter,
      rectanglesOverlap(evidence.selectors.error, evidence.selectors.shutter),
      `${label}: error/shutter overlap calculation`,
    );
    assert.equal(evidence.overlaps.errorWithControls, false, `${label}: error overlaps controls`);
    assert.equal(evidence.overlaps.errorWithShutter, false, `${label}: error overlaps shutter`);
    return;
  }
  if (record.id === "f12-essay-grounding-evidence") {
    const evidence = record.layoutEvidence;
    assert.ok(evidence && typeof evidence === "object", `${label}: missing`);
    assert.equal(evidence.kind, "essay-semantic-title-wrap", `${label}: kind`);
    assertRectangle(evidence.title, `${label}/title`);
    assert.ok(Array.isArray(evidence.phrases), `${label}: phrases`);
    assert.equal(evidence.phrases.length, 2, `${label}: phrase geometry count`);
    assert.equal(evidence.phraseCount, 2, `${label}: phrase count`);
    assert.equal(
      evidence.titleWithinViewport,
      rectangleWithinViewport(evidence.title, profile.width, profile.height),
      `${label}: title viewport calculation`,
    );
    assert.equal(evidence.titleWithinViewport, true, `${label}: title outside viewport`);
    assert.ok(Array.isArray(evidence.phrasesWithinViewport), `${label}: phrase viewport results`);
    assert.equal(evidence.phrasesWithinViewport.length, 2, `${label}: phrase viewport count`);
    for (const [index, rectangle] of evidence.phrases.entries()) {
      assertRectangle(rectangle, `${label}/phrase-${index + 1}`);
      assert.equal(
        evidence.phrasesWithinViewport[index],
        rectangleWithinViewport(rectangle, profile.width, profile.height),
        `${label}/phrase-${index + 1}: viewport calculation`,
      );
      assert.equal(
        evidence.phrasesWithinViewport[index],
        true,
        `${label}/phrase-${index + 1}: outside viewport`,
      );
    }
    return;
  }
  assert.equal(record.layoutEvidence, null, `${label}: unexpected`);
}

function rectangleWithinViewport(rectangle, viewportWidth, viewportHeight) {
  return (
    rectangle.left >= -1 &&
    rectangle.top >= -1 &&
    rectangle.left + rectangle.width <= viewportWidth + 1 &&
    rectangle.top + rectangle.height <= viewportHeight + 1
  );
}

function rectanglesOverlap(left, right) {
  return !(
    left.left + left.width <= right.left ||
    right.left + right.width <= left.left ||
    left.top + left.height <= right.top ||
    right.top + right.height <= left.top
  );
}

function pngDimensions(buffer, filePath) {
  assert.ok(buffer.length >= 24, `Invalid PNG: ${filePath}`);
  assert.deepEqual(
    [...buffer.subarray(0, 8)],
    [137, 80, 78, 71, 13, 10, 26, 10],
    `Invalid PNG signature: ${filePath}`,
  );
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}
