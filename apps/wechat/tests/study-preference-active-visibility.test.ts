import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import templateRaw from "../miniprogram/pages/study-preference/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import stylesRaw from "../miniprogram/pages/study-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import logicRaw from "../miniprogram/pages/study-preference/index.ts?raw";
import {
  studyMinutesOptionId,
  studyMinutesScrollTargets,
} from "../miniprogram/pages/study-preference/scroll-position";

const template = String(templateRaw);
const styles = String(stylesRaw);
const logic = String(logicRaw);

describe("study-preference active option visibility", () => {
  it.each([30, 150, 720])(
    "maps %i minutes to deterministic weekday and weekend anchors",
    (minutes) => {
      expect(studyMinutesOptionId("weekdayMinutes", minutes)).toBe(
        `weekday-minutes-${String(minutes)}`,
      );
      expect(studyMinutesOptionId("weekendMinutes", minutes)).toBe(
        `weekend-minutes-${String(minutes)}`,
      );
    },
  );

  it("recomputes both anchors from restored data on every page entry", () => {
    const firstEntry = studyMinutesScrollTargets({
      weekdayMinutes: 30,
      weekendMinutes: 150,
    });
    const changed = studyMinutesScrollTargets({
      weekdayMinutes: 720,
      weekendMinutes: 30,
    });
    const reentered = studyMinutesScrollTargets({
      weekdayMinutes: 30,
      weekendMinutes: 150,
    });

    expect(firstEntry).toEqual({
      weekdayMinutesTarget: "weekday-minutes-30",
      weekendMinutesTarget: "weekend-minutes-150",
    });
    expect(changed).toEqual({
      weekdayMinutesTarget: "weekday-minutes-720",
      weekendMinutesTarget: "weekend-minutes-30",
    });
    expect(reentered).toEqual(firstEntry);
  });

  it("binds each horizontal rail to its active option and retains a browsing cue", () => {
    expect(template).toContain('scroll-into-view="{{weekdayMinutesTarget}}"');
    expect(template).toContain('scroll-into-view="{{weekendMinutesTarget}}"');
    expect(template).toContain('id="weekday-minutes-{{item.minutes}}"');
    expect(template).toContain('id="weekend-minutes-{{item.minutes}}"');
    expect(template).toContain("左右滑动查看更多时长");
    expect(styles).toMatch(/\.time-options__hint\s*\{/u);
  });

  it("explains daily override precedence and returns to Today for an immediate replan read", () => {
    expect(template).toContain("今天继续以手动时间为准");
    expect(template).toContain("固定恢复日仍会立即影响今天的任务类型");
    expect(styles).toMatch(/\.preference-scope-note\s*\{/u);
    expect(logic).toContain('title: "已更新，正在重排"');
    expect(logic).toContain("wx.switchTab({");
    expect(logic).toContain("url: routes.today");
  });
});
