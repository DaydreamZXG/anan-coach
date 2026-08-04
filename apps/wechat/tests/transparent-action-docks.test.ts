import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import ananStyles from "../miniprogram/pages/anan/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import stage9Styles from "../miniprogram/design-system/stage9/screen.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import taskShellStyles from "../miniprogram/task-runtime/task-shell/index.wxss?raw";

describe("transparent action docks", () => {
  it("岸岸输入栏只保留输入胶囊，不再铺整块底部蒙版", () => {
    expect(ananStyles).toMatch(
      /\.companion-composer\s*\{[^}]*background:\s*transparent;[^}]*pointer-events:\s*none;/u,
    );
    expect(ananStyles).toMatch(/\.composer-surface\s*\{[^}]*pointer-events:\s*auto;/u);
  });

  it("通用操作坞和做题页底栏只保留可点击控件", () => {
    for (const styles of [stage9Styles, taskShellStyles]) {
      expect(styles).toContain("background: transparent;");
      expect(styles).toContain("box-shadow: none;");
      expect(styles).toContain("pointer-events: none;");
      expect(styles).toContain("pointer-events: auto;");
    }
  });
});
