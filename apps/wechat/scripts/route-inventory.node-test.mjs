import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assertBusinessCoverage,
  assertRegisteredPageArtifacts,
  assertRegisteredPageComponents,
  routeInventory,
} from "./app-pages.mjs";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const appConfig = readJson(resolve(projectRoot, "miniprogram/app.json"));
const routeManifest = readJson(
  resolve(projectRoot, "miniprogram/app/navigation/routes.manifest.json"),
);

test("classifies all registered routes as 58 business pages and 6 asset entries", () => {
  const inventory = routeInventory(appConfig, routeManifest);

  assert.equal(inventory.allRoutes.length, 64);
  assert.equal(inventory.businessRoutes.length, 58);
  assert.equal(inventory.assetEntryRoutes.length, 6);
});

test("preserves the route inventory when a business page moves between main and subpackage", () => {
  const before = {
    pages: ["pages/login/index"],
    subPackages: [
      { root: "pages/profile", pages: ["qualification/index"] },
      { root: "packages/anan-thinking", pages: ["asset-entry/index"] },
    ],
  };
  const after = {
    pages: ["pages/login/index", "pages/profile/qualification/index"],
    subPackages: [{ root: "packages/anan-thinking", pages: ["asset-entry/index"] }],
  };
  const manifest = {
    businessPages: ["pages/login/index", "pages/profile/qualification/index"],
    assetEntryPages: ["packages/anan-thinking/asset-entry/index"],
  };

  assert.deepEqual(routeInventory(before, manifest), routeInventory(after, manifest));
});

test("reports the exact missing business-page artifact", () => {
  const inventory = routeInventory(
    { pages: ["pages/login/index"] },
    { businessPages: ["pages/login/index"], assetEntryPages: [] },
  );

  assert.throws(
    () => assertRegisteredPageArtifacts(inventory, (_route, extension) => extension !== "wxss"),
    /Missing registered page artifact: pages\/login\/index\.wxss/u,
  );
});

test("reports the exact missing asset-entry artifact", () => {
  const inventory = routeInventory(
    {
      pages: ["pages/login/index"],
      subPackages: [{ root: "packages/anan-thinking", pages: ["asset-entry/index"] }],
    },
    {
      businessPages: ["pages/login/index"],
      assetEntryPages: ["packages/anan-thinking/asset-entry/index"],
    },
  );

  assert.throws(
    () =>
      assertRegisteredPageArtifacts(
        inventory,
        (route, extension) =>
          route !== "packages/anan-thinking/asset-entry/index" || extension !== "wxml",
      ),
    /Missing registered page artifact: packages\/anan-thinking\/asset-entry\/index\.wxml/u,
  );
});

test("reports the exact missing asset-entry component dependency", () => {
  const inventory = routeInventory(
    {
      pages: ["pages/login/index"],
      subPackages: [{ root: "packages/anan-thinking", pages: ["asset-entry/index"] }],
    },
    {
      businessPages: ["pages/login/index"],
      assetEntryPages: ["packages/anan-thinking/asset-entry/index"],
    },
  );

  assert.throws(
    () =>
      assertRegisteredPageComponents(inventory, (route) =>
        route === "packages/anan-thinking/asset-entry/index"
          ? [
              {
                name: "anan-visual",
                root: "packages/anan-thinking/components/visual/index",
                missingExtensions: ["wxml"],
              },
            ]
          : [],
      ),
    /Registered page component anan-visual is incomplete: packages\/anan-thinking\/components\/visual\/index\.wxml/u,
  );
});

test("reports the exact business route when its visual scenario is deleted", () => {
  const inventory = routeInventory(
    { pages: ["pages/login/index", "pages/today/index"] },
    { businessPages: ["pages/login/index", "pages/today/index"], assetEntryPages: [] },
  );

  assert.throws(
    () => assertBusinessCoverage(inventory, ["pages/login/index"], "Visual coverage"),
    /Visual coverage missing business routes:\npages\/today\/index/u,
  );
});

test("reports the exact business route when its interaction contract is deleted", () => {
  const inventory = routeInventory(
    { pages: ["pages/login/index", "pages/today/index"] },
    { businessPages: ["pages/login/index", "pages/today/index"], assetEntryPages: [] },
  );

  assert.throws(
    () => assertBusinessCoverage(inventory, ["pages/login/index"], "Interaction coverage"),
    /Interaction coverage missing business routes:\npages\/today\/index/u,
  );
});

test("keeps asset entries explicit N/A instead of admitting them to business coverage", () => {
  const inventory = routeInventory(
    {
      pages: ["pages/login/index"],
      subPackages: [{ root: "packages/anan-thinking", pages: ["asset-entry/index"] }],
    },
    {
      businessPages: ["pages/login/index"],
      assetEntryPages: ["packages/anan-thinking/asset-entry/index"],
    },
  );

  assert.doesNotThrow(() =>
    assertBusinessCoverage(inventory, ["pages/login/index"], "Visual coverage"),
  );
  assert.throws(
    () =>
      assertBusinessCoverage(
        inventory,
        ["pages/login/index", "packages/anan-thinking/asset-entry/index"],
        "Visual coverage",
      ),
    /Visual coverage must only cover business routes: packages\/anan-thinking\/asset-entry\/index/u,
  );
});

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}
