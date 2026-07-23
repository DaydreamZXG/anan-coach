import { authedV2, idemKey } from "../../../api/endpoints";
import type {
  QualificationFact,
  QualificationFactsUpdate,
  QualificationProfile,
} from "../../../api/generated/types.gen";

interface YearsOption {
  label: string;
  value: number;
}

type FactValue = string | number | boolean | Array<string>;

const EDUCATION_OPTIONS = ["大专", "本科", "硕士", "博士"];
const POLITICAL_OPTIONS = ["群众", "共青团员", "中共党员（含预备）", "民主党派", "其他"];
const YEARS_OPTIONS: YearsOption[] = [
  { label: "无", value: 0 },
  { label: "1 年", value: 1 },
  { label: "2 年", value: 2 },
  { label: "3 年及以上", value: 3 },
];

/** 从 facts 中取某个 key 的当前值（取数组中最后出现的一条） */
function factValue(
  facts: QualificationFact[],
  key: QualificationFact["key"],
): FactValue | undefined {
  const hit = facts.filter((f) => f.key === key).pop();
  return hit ? hit.value : undefined;
}

Page({
  data: {
    statusBarHeight: 20,
    viewState: "loading" as "loading" | "error" | "ready",
    educationOptions: EDUCATION_OPTIONS,
    politicalOptions: POLITICAL_OPTIONS,
    yearsOptions: YEARS_OPTIONS,
    education: "",
    politicalStatus: "",
    freshGraduate: null as boolean | null,
    grassrootsYears: null as number | null,
    major: "",
    canSubmit: false,
    submitting: false,
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.load();
  },

  onBack() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/profile/index" });
      },
    });
  },

  onRetry() {
    void this.load();
  },

  async load() {
    this.setData({ viewState: "loading" });
    try {
      const profile = await authedV2<QualificationProfile>({
        method: "GET",
        path: "/api/v2/qualification/profile",
      });
      const facts = profile.facts;
      const education = factValue(facts, "education");
      const political = factValue(facts, "political_status");
      const fresh = factValue(facts, "fresh_graduate");
      const years = factValue(facts, "grassroots_years");
      const major = factValue(facts, "major");

      const educationText = typeof education === "string" ? education : "";
      const politicalText = typeof political === "string" ? political : "";
      const yearsValue = typeof years === "number" ? years : null;

      // 后端值不在预置选项中时，追加为选项，避免回填丢值
      const educationOptions = [...EDUCATION_OPTIONS];
      if (educationText && !educationOptions.includes(educationText)) {
        educationOptions.push(educationText);
      }
      const politicalOptions = [...POLITICAL_OPTIONS];
      if (politicalText && !politicalOptions.includes(politicalText)) {
        politicalOptions.push(politicalText);
      }
      const yearsOptions = [...YEARS_OPTIONS];
      if (yearsValue !== null && !yearsOptions.some((o) => o.value === yearsValue)) {
        yearsOptions.push({ label: `${yearsValue} 年`, value: yearsValue });
      }

      this.setData({
        viewState: "ready",
        educationOptions,
        politicalOptions,
        yearsOptions,
        education: educationText,
        politicalStatus: politicalText,
        freshGraduate: typeof fresh === "boolean" ? fresh : null,
        grassrootsYears: yearsValue,
        major: typeof major === "string" ? major : "",
      });
      this.refreshCanSubmit();
    } catch {
      this.setData({ viewState: "error" });
    }
  },

  onPickEducation(e: WechatMiniprogram.TouchEvent) {
    this.setData({ education: e.currentTarget.dataset.value as string });
    this.refreshCanSubmit();
  },

  onPickPolitical(e: WechatMiniprogram.TouchEvent) {
    this.setData({ politicalStatus: e.currentTarget.dataset.value as string });
    this.refreshCanSubmit();
  },

  onPickFresh(e: WechatMiniprogram.TouchEvent) {
    this.setData({ freshGraduate: e.currentTarget.dataset.value as boolean });
    this.refreshCanSubmit();
  },

  onPickYears(e: WechatMiniprogram.TouchEvent) {
    this.setData({ grassrootsYears: e.currentTarget.dataset.value as number });
    this.refreshCanSubmit();
  },

  onMajorInput(e: WechatMiniprogram.Input) {
    this.setData({ major: e.detail.value });
    this.refreshCanSubmit();
  },

  refreshCanSubmit() {
    const d = this.data;
    const canSubmit =
      d.education !== "" &&
      d.politicalStatus !== "" &&
      d.freshGraduate !== null &&
      d.grassrootsYears !== null &&
      d.major.trim() !== "";
    this.setData({ canSubmit });
  },

  async onSubmit() {
    if (!this.data.canSubmit || this.data.submitting) return;
    const d = this.data;
    const body: QualificationFactsUpdate = {
      facts: [
        { key: "education", value: d.education },
        { key: "political_status", value: d.politicalStatus },
        { key: "fresh_graduate", value: d.freshGraduate ?? false },
        { key: "grassroots_years", value: d.grassrootsYears ?? 0 },
        { key: "major", value: d.major.trim() },
      ],
    };
    this.setData({ submitting: true });
    try {
      await authedV2<QualificationProfile>({
        method: "PUT",
        path: "/api/v2/qualification/profile",
        body,
        idempotencyKey: idemKey(),
      });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
