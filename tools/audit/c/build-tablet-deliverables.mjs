import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const auditRoot = path.resolve(repositoryRoot, "audit/subagents/c");
const aggregatePath = path.resolve(auditRoot, "manifest.json");
const deviceCoveragePath = path.resolve(auditRoot, "device-coverage.csv");
const uiCoveragePath = path.resolve(auditRoot, "ui-coverage.csv");
const findingsPath = path.resolve(auditRoot, "findings.jsonl");

const portraitPaths = [
  "audit/subagents/c/logs/tablet-ipad-landscape-b1-manifest.json",
  "audit/subagents/c/logs/tablet-ipad-landscape-b2-manifest.json",
];
const landscapePaths = [
  "audit/subagents/c/logs/tablet-ipad-landscape-real-b1-manifest.json",
  "audit/subagents/c/logs/tablet-ipad-landscape-real-b2-manifest.json",
];

const aggregate = JSON.parse(await readFile(aggregatePath, "utf8"));
const portraitManifests = await readManifests(portraitPaths);
const landscapeManifests = await readManifests(landscapePaths);

const portrait = buildTabletProfile({
  key: "tabletPortrait",
  deviceClass: "tablet-portrait",
  manifests: portraitManifests,
  manifestPaths: portraitPaths,
  expected: { width: 768, height: 1024, orientation: "portrait" },
  canonicalRecords: aggregate.records,
  historicalProfileNameNote:
    "The raw profile names contain 'landscape' from an early menu-label assumption. " +
    "Runtime systemInfo and every route record prove 768x1024 portrait; runtime evidence is authoritative.",
});
const landscape = buildTabletProfile({
  key: "tabletLandscape",
  deviceClass: "tablet-landscape",
  manifests: landscapeManifests,
  manifestPaths: landscapePaths,
  expected: { width: 1024, height: 768, orientation: "landscape" },
  canonicalRecords: aggregate.records,
});

aggregate.generatedAt = new Date().toISOString();
aggregate.counts = {
  ...aggregate.counts,
  tabletPortraitScenarioCount: portrait.scenarioCount,
  tabletPortraitCapturedCount: portrait.capturedCount,
  tabletPortraitFailedCount: portrait.failedCount,
  tabletPortraitBusinessScenarioCount: portrait.businessScenarioCount,
  tabletPortraitAssetEntryCount: portrait.assetEntryCount,
  tabletPortraitBusinessRouteCount: portrait.businessRouteCount,
  tabletPortraitRegisteredRouteCount: portrait.registeredRouteCount,
  tabletLandscapeScenarioCount: landscape.scenarioCount,
  tabletLandscapeCapturedCount: landscape.capturedCount,
  tabletLandscapeFailedCount: landscape.failedCount,
  tabletLandscapeBusinessScenarioCount: landscape.businessScenarioCount,
  tabletLandscapeAssetEntryCount: landscape.assetEntryCount,
  tabletLandscapeBusinessRouteCount: landscape.businessRouteCount,
  tabletLandscapeRegisteredRouteCount: landscape.registeredRouteCount,
};
aggregate.manifestPaths = {
  ...aggregate.manifestPaths,
  tabletPortrait: portraitPaths,
  tabletLandscape: landscapePaths,
};
aggregate.profiles = {
  ...aggregate.profiles,
  tabletPortrait: portrait,
  tabletLandscape: landscape,
};
aggregate.tabletRuntimeClassification = {
  authority: "wx.systemInfo for the initial session and routeSystemInfo for every captured record",
  portrait: {
    rawProfileNames: portrait.rawProfileNames,
    runtimeSize: "768x1024",
    deviceOrientation: "portrait",
    historicalProfileNamesMisleading: true,
  },
  landscape: {
    rawProfileNames: landscape.rawProfileNames,
    runtimeSize: "1024x768",
    deviceOrientation: "landscape",
    historicalProfileNamesMisleading: false,
  },
};

await writeFile(aggregatePath, `${JSON.stringify(aggregate, null, 2)}\n`, "utf8");
await updateDeviceCoverage(portrait, landscape);
await writeUiCoverage(aggregate);
await updateFindings();

process.stdout.write(
  `Updated ${path.relative(repositoryRoot, aggregatePath)}, ` +
    `${path.relative(repositoryRoot, deviceCoveragePath)}, ` +
    `${path.relative(repositoryRoot, uiCoveragePath)}, and ` +
    `${path.relative(repositoryRoot, findingsPath)}.\n`,
);

async function readManifests(paths) {
  return Promise.all(
    paths.map(async (relativePath) => {
      const manifest = JSON.parse(
        await readFile(path.resolve(repositoryRoot, relativePath), "utf8"),
      );
      return { relativePath, manifest };
    }),
  );
}

function buildTabletProfile({
  key,
  deviceClass,
  manifests,
  manifestPaths,
  expected,
  canonicalRecords,
  historicalProfileNameNote = "",
}) {
  assert.equal(manifests.length, 2);
  const rawRecords = manifests.flatMap(({ manifest }) => manifest.records);
  const recordById = new Map(rawRecords.map((record) => [record.id, record]));
  assert.equal(recordById.size, 94, `${key}: expected 94 unique states`);
  assert.equal(rawRecords.length, 94, `${key}: duplicate state IDs across batches`);
  assert.deepEqual(
    new Set(recordById.keys()),
    new Set(canonicalRecords.map((record) => record.id)),
    `${key}: state inventory differs from the canonical 94-state inventory`,
  );

  for (const { relativePath, manifest } of manifests) {
    assert.equal(manifest.scenarioCount, manifest.capturedCount, `${relativePath}: incomplete`);
    assert.equal(manifest.failedCount, 0, `${relativePath}: failed captures`);
    assert.equal(manifest.runtimeFailureCount, 0, `${relativePath}: runtime failures`);
    assert.equal(manifest.actionableWarningCount, 0, `${relativePath}: actionable warnings`);
    assert.equal(manifest.fatalError, null, `${relativePath}: fatal error`);
    assert.equal(manifest.systemInfo.windowWidth, expected.width, `${relativePath}: width`);
    assert.equal(manifest.systemInfo.windowHeight, expected.height, `${relativePath}: height`);
    assert.equal(
      manifest.systemInfo.deviceOrientation,
      expected.orientation,
      `${relativePath}: orientation`,
    );
  }

  const records = canonicalRecords.map(({ id }) => recordById.get(id));
  for (const record of records) {
    assert.ok(record.status.startsWith("captured"), `${key}/${record.id}: not captured`);
    assert.equal(record.routeSystemInfo.windowWidth, expected.width, `${key}/${record.id}: width`);
    assert.equal(record.routeSystemInfo.windowHeight, expected.height, `${key}/${record.id}: height`);
    assert.equal(
      record.routeSystemInfo.deviceOrientation,
      expected.orientation,
      `${key}/${record.id}: orientation`,
    );
    assert.equal(
      record.activeRoute,
      record.routePath.replace(/^\//, ""),
      `${key}/${record.id}: active route`,
    );
  }

  const businessRecords = records.filter((record) => !record.id.startsWith("z-anan-"));
  const assetRecords = records.filter((record) => record.id.startsWith("z-anan-"));
  const allRoutes = new Set(records.map((record) => record.routePath));
  const businessRoutes = new Set(businessRecords.map((record) => record.routePath));
  assert.equal(businessRecords.length, 88);
  assert.equal(assetRecords.length, 6);
  assert.equal(businessRoutes.size, 58);
  assert.equal(allRoutes.size, 64);

  const firstManifest = manifests[0].manifest;
  return {
    deviceClass,
    model: firstManifest.systemInfo.model,
    systemInfo: firstManifest.systemInfo,
    systemChrome: firstManifest.systemChrome,
    menuNominalSize: `${expected.width}x${expected.height}`,
    runtimeClassification: `${expected.width}x${expected.height} ${expected.orientation}`,
    runtimeClassificationAuthority:
      "manifest.systemInfo plus routeSystemInfo on all 94 records",
    rawProfileNames: manifests.map(({ manifest }) => manifest.profile),
    sourceManifestPaths: manifestPaths,
    ...(historicalProfileNameNote ? { historicalProfileNameNote } : {}),
    scenarioCount: records.length,
    businessScenarioCount: businessRecords.length,
    assetEntryCount: assetRecords.length,
    capturedCount: records.filter((record) => record.status.startsWith("captured")).length,
    failedCount: records.filter((record) => record.status === "failed").length,
    businessRouteCount: businessRoutes.size,
    registeredRouteCount: allRoutes.size,
    pngDimensionSets: [
      ...new Set(records.map((record) => `${record.screenshotWidth}x${record.screenshotHeight}`)),
    ].sort(),
    records,
  };
}

async function updateDeviceCoverage(portraitProfile, landscapeProfile) {
  const current = (await readFile(deviceCoveragePath, "utf8"))
    .trimEnd()
    .split("\n");
  const header = current[0];
  const retained = current
    .slice(1)
    .filter((line) => !line.startsWith("tablet-portrait-") && !line.startsWith("tablet-landscape-"));
  const rows = [
    tabletCoverageRow("tablet-portrait-business", portraitProfile, "business"),
    tabletCoverageRow("tablet-portrait-assets", portraitProfile, "asset-entry"),
    tabletCoverageRow("tablet-landscape-business", landscapeProfile, "business"),
    tabletCoverageRow("tablet-landscape-assets", landscapeProfile, "asset-entry"),
  ];
  await writeFile(deviceCoveragePath, `${[header, ...retained, ...rows].join("\n")}\n`, "utf8");
}

function tabletCoverageRow(profileName, profile, scenarioClass) {
  const isBusiness = scenarioClass === "business";
  return [
    profileName,
    profile.model,
    profile.deviceClass,
    profile.menuNominalSize,
    `${profile.systemInfo.windowWidth}x${profile.systemInfo.windowHeight}`,
    profile.systemInfo.pixelRatio,
    scenarioClass,
    isBusiness ? profile.businessScenarioCount : profile.assetEntryCount,
    isBusiness ? profile.businessScenarioCount : profile.assetEntryCount,
    0,
    isBusiness ? profile.businessRouteCount : profile.assetEntryCount,
    profile.pngDimensionSets.join(";"),
    isBusiness
      ? "all existing business visual scenarios"
      : "all registered non-business asset entry routes",
    `audit/subagents/c/manifest.json#profiles.${profile.deviceClass === "tablet-portrait" ? "tabletPortrait" : "tabletLandscape"}`,
    profile.sourceManifestPaths
      .map((manifestPath) => manifestPath.replace("/logs/", "/screenshots/").replace("-manifest.json", ""))
      .join(" + "),
  ]
    .map(csvValue)
    .join(",");
}

async function writeUiCoverage(updatedAggregate) {
  const profileMaps = Object.fromEntries(
    Object.entries(updatedAggregate.profiles).map(([key, profile]) => [
      key,
      new Map(profile.records.map((record) => [record.id, record])),
    ]),
  );
  const header = [
    "scenario_id",
    "scenario_class",
    "route",
    "route_path",
    "standard_status",
    "standard_screenshot",
    "standard_png",
    "small_status",
    "small_screenshot",
    "small_png",
    "tall_status",
    "tall_screenshot",
    "tall_png",
    "tablet_portrait_status",
    "tablet_portrait_screenshot",
    "tablet_portrait_png",
    "tablet_portrait_orientation",
    "tablet_landscape_status",
    "tablet_landscape_screenshot",
    "tablet_landscape_png",
    "tablet_landscape_orientation",
  ];
  const rows = updatedAggregate.records.map((canonicalRecord) => {
    const standardRecord = profileMaps.standard.get(canonicalRecord.id);
    const smallRecord = profileMaps.small.get(canonicalRecord.id);
    const tallRecord = profileMaps.tall.get(canonicalRecord.id);
    const portraitRecord = profileMaps.tabletPortrait.get(canonicalRecord.id);
    const landscapeRecord = profileMaps.tabletLandscape.get(canonicalRecord.id);
    return [
      canonicalRecord.id,
      canonicalRecord.id.startsWith("z-anan-") ? "asset-entry" : "business",
      canonicalRecord.route,
      canonicalRecord.routePath,
      ...coverageFields(standardRecord),
      ...coverageFields(smallRecord),
      ...coverageFields(tallRecord),
      ...coverageFields(portraitRecord),
      portraitRecord.routeSystemInfo.deviceOrientation,
      ...coverageFields(landscapeRecord),
      landscapeRecord.routeSystemInfo.deviceOrientation,
    ]
      .map(csvValue)
      .join(",");
  });
  await writeFile(uiCoveragePath, `${[header.join(","), ...rows].join("\n")}\n`, "utf8");
}

function coverageFields(record) {
  if (!record) return ["not-sampled", "", ""];
  return [
    record.status,
    record.screenshot,
    `${record.screenshotWidth}x${record.screenshotHeight}`,
  ];
}

async function updateFindings() {
  const findings = (await readFile(findingsPath, "utf8"))
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const byId = new Map(findings.map((finding) => [finding.id, finding]));
  const captureFinding = byId.get("UI-CAPTURE-001");
  assert.ok(captureFinding, "UI-CAPTURE-001 must exist");

  captureFinding.preconditions = [
    "已登录",
    "微信开发者工具真实渲染",
    "设备为 iPhone 5（systemInfo 320×568）、HUAWEI nova 14 Ultra（systemInfo/window 363×817）、iPad 竖屏（768×1024）或 iPad 横屏（1024×768）",
    "进入默认“单题”拍照态并由 camera 组件返回相机未启动错误",
  ];
  captureFinding.reproductionSteps = [
    "在微信开发者工具选择 iPhone 5，打开 /pages/capture-import/index，等待相机初始化失败反馈出现",
    "观察页面底部“相机没有启动成功，请检查系统相机权限后重试。”提示、相册入口、快门和隐私说明的位置",
    "切换到 HUAWEI nova 14 Ultra、iPad 768×1024 竖屏和 iPad 1024×768 横屏，重复上述步骤",
    "对照 manifest：长安卓快门 top=759.234375、height=69.109375，底部约 828.34 > windowHeight 817；iPad 竖屏快门 top=912、height=72，横屏 top=656、height=72，虽在 viewport 内但截图均显示错误条与相册/快门相交",
  ];
  captureFinding.actualBehavior =
    "四档真实模拟设备均出现橙色错误条覆盖相册文字和快门的情况；iPhone 5 上提示文字溢出胶囊，长安卓上快门约有 11px 越出 window 底部且隐私说明不可见。iPad 竖屏和横屏的快门虽位于 viewport 内，错误条仍横跨相册与快门。顶部“微信发图”仍提供替代路径，因此核心功能并非完全不可用。";
  captureFinding.userImpact =
    "相机失败正是用户最需要理解原因并选择恢复动作的时刻，但错误反馈与操作控件相互遮挡，降低可读性并可能阻断相册/快门点击；该问题跨小屏、长安卓和平板双方向复现。用户仍可改走顶部微信发图，故定级 P2。违反响应式布局、主操作可达性、错误恢复可见性和安全区适配原则。";
  captureFinding.evidence = [
    ...captureFinding.evidence.filter(
      (item) =>
        !item.path.includes("tablet-ipad-landscape-b2") &&
        !item.path.includes("tablet-ipad-landscape-real-b2"),
    ),
    {
      type: "screenshot",
      path: "audit/subagents/c/screenshots/tablet-ipad-landscape-b2/h01-capture-search-idle.png",
      detail:
        "768×1024 iPad portrait 实际截图；历史 raw profile 名含 landscape，但 manifest systemInfo/routeSystemInfo 均为 portrait；错误条覆盖相册与快门。",
    },
    {
      type: "manifest",
      path: "audit/subagents/c/logs/tablet-ipad-landscape-b2-manifest.json",
      detail:
        "h01 captured；iPad 768×1024、deviceOrientation=portrait；快门 top=912、height=72、bottom=984。",
    },
    {
      type: "screenshot",
      path: "audit/subagents/c/screenshots/tablet-ipad-landscape-real-b2/h01-capture-search-idle.png",
      detail:
        "1024×768 iPad landscape 实际截图；错误条横跨相册与快门。",
    },
    {
      type: "manifest",
      path: "audit/subagents/c/logs/tablet-ipad-landscape-real-b2-manifest.json",
      detail:
        "h01 captured；iPad 1024×768、deviceOrientation=landscape；快门 top=656、height=72、bottom=728。",
    },
  ];
  captureFinding.affectedPagesOrModules = [
    "pages/capture-import/index",
    "pages/capture-import/index.wxml",
    "pages/capture-import/index.wxss",
    "相机失败错误态（已在 iPhone 5、HUAWEI nova 14 Ultra、iPad 竖屏与 iPad 横屏复现）",
  ];
  captureFinding.recommendedRegressionTest = [
    "在 iPhone 5 320×568、HUAWEI nova 14 Ultra 363×817、iPad portrait 768×1024 与 iPad landscape 1024×768 重放 camera init error",
    "断言错误文案完整位于容器内",
    "断言错误条与相册入口、.camera-control、.camera-shutter 几何交集均为 0",
    "断言快门 bottom ≤ safeArea.bottom 且隐私说明与替代入口可见、可点击",
    "在标准 iPhone 12/13 Pro 回归正常相机态、权限拒绝态和相册路径",
  ];

  const answerCardFinding = {
    id: "UI-PRACTICE-ANSWER-CARD-001",
    severity: "P2",
    confidence: "high",
    category: "UI/UX-responsive-layout",
    functionalDomain: "刷题",
    pageOrFlow: "/pages/practice/index；答题中的“答题卡”底部浮层",
    preconditions: [
      "已登录并进入刷题 answering 状态",
      "微信开发者工具选择 iPad 竖屏，systemInfo/window 为 768×1024",
      "打开答题卡",
    ],
    reproductionSteps: [
      "在 iPad 768×1024 portrait 打开 /pages/practice/index?mode=targeted&planTaskId=visual-plan-task-001",
      "触发“答题卡”并观察浮层右侧的图例、题目统计与“继续第 3 题”主按钮",
      "对照 manifest 几何：.answer-card__sheet left=280、width=560，right=840 > windowWidth 768；.answer-card__cta left=300、width=520，right=820 > 768",
      "切换同一 iPad 到 1024×768 landscape 重放；此时 sheet right=840、CTA right=820，均不越界",
    ],
    expectedBehavior:
      "答题卡浮层应在所有支持的平板方向内完整居中或受 viewport 约束；图例、状态摘要、关闭入口和主按钮均应完整可读、可点击。",
    actualBehavior:
      "iPad 竖屏下浮层整体向右偏移，右侧 72px 超出 viewport；右侧图例/统计被裁切，主按钮右侧 52px 被裁切。1024×768 横屏不越界，标准 390px 手机布局也不触发该断点问题。",
    userImpact:
      "用户在刷题过程中无法完整读取答题卡右侧状态，主操作的部分点击区域不可见，增加误解与误触风险；关闭与继续仍有可见区域，存在替代操作空间，故定级 P2。违反响应式容器约束、信息完整性和主操作可达性原则。",
    evidence: [
      {
        type: "screenshot",
        path: "audit/subagents/c/screenshots/tablet-ipad-landscape-b1/c09-xingce-answer-card.png",
        detail:
          "768×1024 iPad portrait 实际截图；右侧图例、内容与 CTA 明显被 viewport 裁切。历史 raw profile 名误含 landscape，运行时为 portrait。",
      },
      {
        type: "manifest",
        path: "audit/subagents/c/logs/tablet-ipad-landscape-b1-manifest.json",
        detail:
          "c09 captured；deviceOrientation=portrait；sheet left=280,width=560,right=840；CTA left=300,width=520,right=820；semanticHorizontalOverflow/primaryHorizontalOverflow 均为 true。",
      },
      {
        type: "screenshot",
        path: "audit/subagents/c/screenshots/tablet-ipad-landscape-real-b1/c09-xingce-answer-card.png",
        detail:
          "1024×768 iPad landscape 对照截图；答题卡内容与 CTA 完整可见。",
      },
      {
        type: "manifest",
        path: "audit/subagents/c/logs/tablet-ipad-landscape-real-b1-manifest.json",
        detail:
          "横屏 c09 sheet right=840、CTA right=820，semanticHorizontalOverflow/primaryHorizontalOverflow 均为 false。",
      },
      {
        type: "source",
        path: "apps/wechat/miniprogram/pages/practice/index.wxss:715",
        detail: "基础规则为 .answer-card__sheet 同时 left:0、right:0。",
      },
      {
        type: "source",
        path: "apps/wechat/miniprogram/pages/practice/index.wxss:1238",
        detail:
          "@media (min-width:768px) 在保留 left:0 的同时设置 right:50%、width:100%、max-width:560px、transform:translateX(50%)。",
      },
    ],
    inferredRootCause:
      "768px 断点的居中规则与基础定位规则叠加后成为过约束布局：left:0 未清除，width/max-width 固定为 560px，再执行 translateX(50%)，最终把浮层左边移到 280px。竖屏可用宽度 768px 时右边达到 840px；横屏宽度 1024px 时同一错误几何尚未越界，因此问题具有方向/临界断点特征。",
    affectedPagesOrModules: [
      "pages/practice/index",
      "pages/practice/index.wxss",
      "答题卡 answer-sheet 状态（iPad 768×1024 portrait 已证实；1024×768 landscape 未越界）",
    ],
    recommendedFixDirection:
      "让平板断点使用单一、可证明的居中约束，例如明确的 50% 左锚点配合负 50% 自身位移，或使用受 viewport padding 约束的 inset/auto margin；清除与新规则冲突的 left/right 值，并保证 768px 临界宽度下 sheet 与 CTA 均不越界。不要用 overflow:hidden 掩盖内容。",
    recommendedRegressionTest: [
      "在 767、768、800 与 1024px 宽度重放 c09 answer-sheet",
      "断言 sheet.left ≥ safeArea.left、sheet.right ≤ safeArea.right",
      "断言 CTA 和关闭按钮完整可见且点击区域不被裁切",
      "回归 iPad portrait/landscape、标准 iPhone 390px，以及 c10 pause/c15 岸岸上下文等同页 overlay 状态",
    ],
    status: "confirmed",
  };
  byId.set(answerCardFinding.id, answerCardFinding);

  const orderedIds = [
    "UI-CAPTURE-001",
    "UI-PRACTICE-ANSWER-CARD-001",
    "UI-ESSAY-TITLE-001",
  ];
  const ordered = orderedIds.map((id) => {
    const finding = byId.get(id);
    assert.ok(finding, `${id} must exist`);
    return finding;
  });
  await writeFile(
    findingsPath,
    `${ordered.map((finding) => JSON.stringify(finding)).join("\n")}\n`,
    "utf8",
  );
}

function csvValue(value) {
  const text = String(value ?? "");
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}
