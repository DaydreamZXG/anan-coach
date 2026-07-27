import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { registeredPages } from "./app-pages.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const miniprogramRoot = path.join(projectRoot, "miniprogram");
const appConfig = JSON.parse(await readFile(path.join(miniprogramRoot, "app.json"), "utf8"));
const tabRoutes = new Set(appConfig.tabBar.list.map((item) => item.pagePath));
const safeTopExceptions = new Map([
  ["pages/login/index", 'safe-top mode="status"'],
  ...Array.from(tabRoutes, (route) => [route, 'safe-top gap="4"']),
]);
const taskShellRoutes = new Set([
  "pages/practice/index",
  "pages/mock-exam/index",
  "pages/essay-task/index",
]);
const forbiddenLegacyTopClasses = [
  "capture-topbar",
  "position-nav",
  "archive-nav",
  "qualification-nav",
  "week-nav",
  "settlement-nav",
  "basis-nav",
  "adjustment-nav",
  "material-header",
  "review-header",
  "reflection-topbar",
  "library-topbar",
  "detail-topbar",
  "back-button",
];

const customRoutes = [];
for (const route of registeredPages(appConfig)) {
  const pageConfigPath = path.join(miniprogramRoot, `${route}.json`);
  const pageConfig = JSON.parse(await readFile(pageConfigPath, "utf8"));
  if (pageConfig.navigationStyle !== "custom") continue;
  customRoutes.push(route);

  const template = await readFile(path.join(miniprogramRoot, `${route}.wxml`), "utf8");
  const safeTopContract = safeTopExceptions.get(route);
  if (safeTopContract) {
    assert.ok(
      template.includes(safeTopContract),
      `${route} must keep its documented ${safeTopContract} exception`,
    );
    assert.ok(
      !template.includes("<workbench-nav"),
      `${route} is a root/immersive exception and must not render a duplicate workbench nav`,
    );
    continue;
  }

  if (taskShellRoutes.has(route)) {
    assert.ok(template.includes("<task-shell"), `${route} must render the shared task shell`);
    assert.ok(
      template.includes('id="runtime-task-shell"'),
      `${route} must expose runtime task-shell geometry`,
    );
    assert.ok(!template.includes("<safe-top"), `${route} must not stack safe-top above task-shell`);
    continue;
  }

  const workbenchTag = template.match(/<workbench-nav\b[^>]*>/u)?.[0] ?? "";
  assert.ok(workbenchTag, `${route} must render the shared workbench nav`);
  assert.ok(
    template.includes('id="runtime-workbench-nav"'),
    `${route} must expose runtime workbench-nav geometry`,
  );
  assert.ok(
    !/\b(?:hidden|wx:if)\s*=/u.test(workbenchTag),
    `${route} must keep its primary workbench nav mounted in every page state`,
  );
  assert.ok(
    !template.includes("<safe-top"),
    `${route} must not stack safe-top above workbench-nav`,
  );
  for (const className of forbiddenLegacyTopClasses) {
    assert.ok(
      !template.includes(`class="${className}`),
      `${route} still renders legacy top chrome .${className}`,
    );
  }
}

assert.equal(
  safeTopExceptions.size,
  5,
  "Only login and the four root tabs may use safe-top without shared navigation",
);
process.stdout.write(
  `Validated ${String(customRoutes.length)} custom-navigation pages: ` +
    `${String(customRoutes.length - safeTopExceptions.size - taskShellRoutes.size)} workbench, ` +
    `${String(taskShellRoutes.size)} task-shell, ${String(safeTopExceptions.size)} documented exceptions.\n`,
);
