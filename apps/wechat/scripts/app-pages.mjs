import assert from "node:assert/strict";

export function registeredPages(appConfig) {
  const mainPages = appConfig.pages ?? [];
  const subpackagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
    ({ root, pages }) => pages.map((page) => `${root.replace(/\/$/u, "")}/${page}`),
  );
  return [...mainPages, ...subpackagePages];
}

/**
 * The app config owns registration. The route manifest classifies each registered
 * route so coverage gates can distinguish user-facing pages from package-only
 * asset entries.
 */
export function routeInventory(appConfig, routeManifest) {
  const allRoutes = registeredPages(appConfig);
  const businessRoutes = routeManifest.businessPages ?? [];
  const assetEntryRoutes = routeManifest.assetEntryPages ?? [];

  assert.equal(
    new Set(allRoutes).size,
    allRoutes.length,
    "app.json registered routes must not contain duplicates",
  );
  assert.deepEqual(
    [...businessRoutes, ...assetEntryRoutes].sort(),
    [...allRoutes].sort(),
    "Route manifest businessPages + assetEntryPages must exactly classify all app.json registered routes",
  );
  assert.equal(
    new Set(businessRoutes).size,
    businessRoutes.length,
    "Route manifest businessPages must not contain duplicates",
  );
  assert.equal(
    new Set(assetEntryRoutes).size,
    assetEntryRoutes.length,
    "Route manifest assetEntryPages must not contain duplicates",
  );
  assert.equal(
    new Set([...businessRoutes, ...assetEntryRoutes]).size,
    businessRoutes.length + assetEntryRoutes.length,
    "Route manifest route categories must not overlap",
  );

  return {
    allRoutes,
    businessRoutes,
    assetEntryRoutes,
  };
}

export function assertBusinessCoverage(inventory, coveredRoutes, coverageName) {
  const covered = new Set(coveredRoutes);
  for (const route of covered) {
    assert.ok(
      inventory.businessRoutes.includes(route),
      `${coverageName} must only cover business routes: ${route}`,
    );
  }

  const missing = inventory.businessRoutes.filter((route) => !covered.has(route));
  assert.deepEqual(missing, [], `${coverageName} missing business routes:\n${missing.join("\n")}`);
}

export function assertRegisteredPageArtifacts(inventory, artifactExists) {
  for (const route of inventory.allRoutes) {
    for (const extension of ["ts", "json", "wxml", "wxss"]) {
      assert.ok(
        artifactExists(route, extension),
        `Missing registered page artifact: ${route}.${extension}`,
      );
    }
  }
}

export function assertRegisteredPageComponents(inventory, componentsForRoute) {
  for (const route of inventory.allRoutes) {
    for (const component of componentsForRoute(route)) {
      for (const extension of component.missingExtensions) {
        assert.fail(
          `Registered page component ${component.name} is incomplete: ${component.root}.${extension}`,
        );
      }
    }
  }
}
