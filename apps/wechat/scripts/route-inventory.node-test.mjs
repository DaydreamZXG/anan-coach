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

test("classifies all 60 registered routes as business pages", () => {
  const inventory = routeInventory(appConfig, routeManifest);

  assert.equal(inventory.allRoutes.length, 60);
  assert.equal(inventory.businessRoutes.length, 60);
  assert.deepEqual(inventory.assetEntryRoutes, []);
});

test("preserves the route inventory when a business page moves between main and subpackage", () => {
  const before = {
    pages: ["pages/login/index"],
    subPackages: [{ root: "pages/onboarding", pages: ["profile/index"] }],
  };
  const after = {
    pages: ["pages/login/index", "pages/onboarding/profile/index"],
    subPackages: [],
  };
  const manifest = {
    businessPages: ["pages/login/index", "pages/onboarding/profile/index"],
    assetEntryPages: [],
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

test("reports the exact missing registered-page component dependency", () => {
  const inventory = routeInventory(
    { pages: ["pages/login/index"] },
    { businessPages: ["pages/login/index"], assetEntryPages: [] },
  );

  assert.throws(
    () =>
      assertRegisteredPageComponents(inventory, (route) =>
        route === "pages/login/index"
          ? [
              {
                name: "login-card",
                root: "components/login-card/index",
                missingExtensions: ["wxml"],
              },
            ]
          : [],
      ),
    /Registered page component login-card is incomplete: components\/login-card\/index\.wxml/u,
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

test("rejects internal asset hosts from both registration and manifest classifications", () => {
  const assetEntry = "packages/anan-thinking/asset-entry/index";

  assert.throws(
    () =>
      routeInventory(
        {
          pages: ["pages/login/index"],
          subPackages: [{ root: "packages/anan-thinking", pages: ["asset-entry/index"] }],
        },
        {
          businessPages: ["pages/login/index", assetEntry],
          assetEntryPages: [],
        },
      ),
    /Internal asset entry sources must not be registered/u,
  );
  assert.throws(
    () =>
      routeInventory(
        { pages: ["pages/login/index"] },
        {
          businessPages: ["pages/login/index"],
          assetEntryPages: [assetEntry],
        },
      ),
    /Internal asset entry sources must not be registered/u,
  );
});

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}
