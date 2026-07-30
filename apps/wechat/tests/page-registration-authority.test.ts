import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import appConfig from "../miniprogram/app.json";
import routeManifest from "../miniprogram/app/navigation/routes.manifest.json";
import { routes } from "../miniprogram/app/navigation/routes";

const assetPackageNames = [
  "anan-thinking",
  "anan-reminder",
  "anan-encouragement",
  "anan-rest",
  "anan-deprioritize",
  "anan-growth",
] as const;
const assetEntryRoutes = assetPackageNames.map(
  (packageName) => `packages/${packageName}/asset-entry/index`,
);

function registeredPages(): string[] {
  const mainPages = appConfig.pages;
  const subpackagePages = appConfig.subPackages.flatMap(({ root, pages }) =>
    pages.map((page) => `${root}/${page}`),
  );
  return [...mainPages, ...subpackagePages];
}

describe("WeChat page registration authority", () => {
  it("registers only typed business routes and keeps every authority in exact set agreement", () => {
    const registered = registeredPages();
    const typedRoutes = Object.values(routes).map((route) => route.replace(/^\//u, ""));

    expect(new Set(registered)).toEqual(new Set(routeManifest.businessPages));
    expect(new Set(registered)).toEqual(new Set(typedRoutes));
    expect(registered).toHaveLength(routeManifest.businessPages.length);
    expect(registered).toHaveLength(typedRoutes.length);
    expect(new Set(registered).size).toBe(registered.length);
  });

  it("keeps Anan visuals in asset-only subpackages without registering pages", () => {
    const registered = registeredPages();
    const assetSubpackages = appConfig.subPackages.filter(({ name }) =>
      assetPackageNames.includes(name as (typeof assetPackageNames)[number]),
    );

    expect(assetSubpackages).toHaveLength(assetPackageNames.length);
    expect(
      assetSubpackages.map(({ name, root, pages, entry }) => ({
        name,
        root,
        pages,
        entry,
      })),
    ).toEqual(
      assetPackageNames.map((packageName) => ({
        name: packageName,
        root: `packages/${packageName}`,
        pages: [],
        entry: "package-entry.js",
      })),
    );
    expect(routeManifest.assetEntryPages).toEqual([]);
    for (const route of assetEntryRoutes) {
      expect(registered).not.toContain(route);
      expect(Object.values(routes)).not.toContain(`/${route}`);
    }
  });

  it("preserves the internal visual source while removing only its public registration", () => {
    const miniprogramRoot = resolve(process.cwd(), "miniprogram");

    for (const packageName of assetPackageNames) {
      for (const extension of ["ts", "json", "wxml", "wxss"]) {
        const entrySource = readFileSync(
          resolve(miniprogramRoot, `packages/${packageName}/asset-entry/index.${extension}`),
          "utf8",
        );
        const visualSource = readFileSync(
          resolve(miniprogramRoot, `packages/${packageName}/components/visual/index.${extension}`),
          "utf8",
        );
        expect(entrySource.length).toBeGreaterThan(0);
        expect(visualSource.length).toBeGreaterThan(0);
      }
    }
  });
});
