import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    exclude: ["**/node_modules/**", "**/dist/**", "cloudfunctions/**"],
    // Page lifecycle suites dynamically reload Mini Program modules. Capping workers
    // prevents transform contention from timing out and leaking unfinished globals.
    maxWorkers: 4,
    testTimeout: 15_000,
  },
});
