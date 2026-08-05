import { describe, expect, it } from "vitest";

import appConfig from "../miniprogram/app.json";
import routeManifest from "../miniprogram/app/navigation/routes.manifest.json";
import { routes } from "../miniprogram/app/navigation/routes";

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
});
