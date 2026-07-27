export function registeredPages(appConfig) {
  const mainPages = appConfig.pages ?? [];
  const subpackagePages = (appConfig.subPackages ?? appConfig.subpackages ?? []).flatMap(
    ({ root, pages }) => pages.map((page) => `${root.replace(/\/$/u, "")}/${page}`),
  );
  return [...mainPages, ...subpackagePages];
}
