import assert from "node:assert/strict";

export function registeredPages(appConfig) {
  const mainPages = appConfig.pages ?? [];
  const subpackagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
    ({ root, pages = [] }) => pages.map((page) => `${root.replace(/\/$/u, "")}/${page}`),
  );
  return [...mainPages, ...subpackagePages];
}

/**
 * app.json is the registration authority. The route manifest and typed route
 * registry enumerate the same user-facing surface for independent validation.
 * Asset component sources may remain in the repository, but must never be
 * admitted as registered pages.
 */
export function routeInventory(appConfig, routeManifest) {
  const allRoutes = registeredPages(appConfig);
  const businessRoutes = routeManifest.businessPages ?? [];
  const assetEntryRoutes = routeManifest.assetEntryPages ?? [];
  const internalAssetHosts = allRoutes.filter((route) =>
    /^packages\/anan-[^/]+\/asset-entry\/index$/u.test(route),
  );

  assert.equal(
    new Set(allRoutes).size,
    allRoutes.length,
    "app.json registered routes must not contain duplicates",
  );
  assert.deepEqual(
    internalAssetHosts,
    [],
    "Internal asset entry sources must not be registered as app pages",
  );
  assert.deepEqual(
    businessRoutes.slice().sort(),
    [...allRoutes].sort(),
    "Route manifest businessPages must exactly enumerate all app.json registered routes",
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
    assetEntryRoutes.length,
    0,
    "Internal asset entry sources must not be registered or listed as public pages",
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
