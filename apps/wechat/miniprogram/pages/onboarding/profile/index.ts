import { ApiClient } from "../../../api/client";
import type { MajorTreeResponse } from "../../../api/generated/types.gen";
import { QualificationApi } from "../../../api/qualification";
import { routes } from "../../../app/navigation/routes";
import { navigateBackOr } from "../shared/app/navigation/safe-back";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  QualificationProfileEditorController,
  type QualificationProfileEditorState,
} from "../features/qualification/profile-editor-controller";
import {
  emptyQualificationDraft,
  qualificationCertificationOptions,
  qualificationFactsFromDraft,
  qualificationGenderOptions,
  qualificationPoliticalStatusOptions,
  toggleQualificationCertification,
  type QualificationDraft,
} from "../../../features/qualification/onboarding";
import { createIdempotencyKey } from "../../../platform/command-id";

interface GraduationOption {
  label: string;
  year: number;
  freshGraduate: "true" | "false";
}

interface MajorOption {
  code: string;
  name: string;
  category: string;
}

interface CertificationOptionView {
  value: string;
  label: string;
  selected: boolean;
}

interface QualificationProfilePageData extends QualificationDraft {
  pageTitle: string;
  educationOptions: string[];
  degreeOptions: string[];
  graduationOptions: GraduationOption[];
  serviceProjectOptions: string[];
  genderOptions: readonly string[];
  politicalStatusOptions: readonly string[];
  certificationOptions: CertificationOptionView[];
  majorSuggestions: MajorOption[];
  majorCategory: string;
  graduationLabel: string;
  serviceExpanded: boolean;
  error: string;
  requestId: string;
  saving: boolean;
  savingDraft: boolean;
  profileVersion: number;
  loadingProfile: boolean;
  loadFailed: boolean;
  catalogLoading: boolean;
  catalogError: string;
  pendingRecovery: boolean;
  primaryDisabled: boolean;
  primaryActionLabel: string;
}

interface QualificationProfilePageCustom {
  back(): void;
  retryLoad(): void;
  retryCatalog(): void;
  inputMajor(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  selectMajor(event: WechatMiniprogram.BaseEvent): void;
  chooseEducation(event: WechatMiniprogram.PickerChange): void;
  chooseDegree(event: WechatMiniprogram.PickerChange): void;
  chooseGraduation(event: WechatMiniprogram.PickerChange): void;
  chooseGender(event: WechatMiniprogram.BaseEvent): void;
  choosePoliticalStatus(event: WechatMiniprogram.BaseEvent): void;
  toggleCertification(event: WechatMiniprogram.BaseEvent): void;
  chooseServiceMode(event: WechatMiniprogram.BaseEvent): void;
  chooseServiceProject(event: WechatMiniprogram.BaseEvent): void;
  chooseWorkYears(event: WechatMiniprogram.BaseEvent): void;
  chooseGrassrootsYears(event: WechatMiniprogram.BaseEvent): void;
  inputHukou(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  submit(): void;
  saveAndExit(): void;
  markLater(): void;
}

type QualificationProfilePage = WechatMiniprogram.Page.Instance<
  QualificationProfilePageData,
  QualificationProfilePageCustom
>;

const educationOptions = ["大专", "本科", "硕士", "博士"];
const degreeOptions = ["无学位", "学士", "硕士", "博士"];
const currentGraduationYear = new Date().getFullYear();
const graduationOptions: GraduationOption[] = [
  {
    label: `${String(currentGraduationYear)}（应届）`,
    year: currentGraduationYear,
    freshGraduate: "true",
  },
  {
    label: `${String(currentGraduationYear - 1)}（择业期应届）`,
    year: currentGraduationYear - 1,
    freshGraduate: "true",
  },
  {
    label: `${String(currentGraduationYear - 1)}（社会人员）`,
    year: currentGraduationYear - 1,
    freshGraduate: "false",
  },
  {
    label: `${String(currentGraduationYear - 2)} 及以前`,
    year: currentGraduationYear - 2,
    freshGraduate: "false",
  },
];
const serviceProjectOptions = [
  "大学生村官",
  "三支一扶",
  "特岗教师",
  "西部计划",
  "高校毕业生退役士兵",
  "其他服务基层项目",
];

const runtimes = new PageControllerRegistry<
  QualificationProfilePage,
  QualificationProfileEditorController
>();
const catalogs = new WeakMap<QualificationProfilePage, MajorOption[]>();
const partialSaveKeys = new WeakMap<QualificationProfilePage, string>();

Page<QualificationProfilePageData, QualificationProfilePageCustom>({
  data: {
    ...emptyQualificationDraft,
    pageTitle: "我的报考信息",
    educationOptions,
    degreeOptions,
    graduationOptions,
    serviceProjectOptions,
    genderOptions: qualificationGenderOptions,
    politicalStatusOptions: qualificationPoliticalStatusOptions,
    certificationOptions: certificationViews([]),
    majorSuggestions: [],
    majorCategory: "",
    graduationLabel: "",
    serviceExpanded: false,
    error: "",
    requestId: "",
    saving: false,
    savingDraft: false,
    profileVersion: 0,
    loadingProfile: true,
    loadFailed: false,
    catalogLoading: true,
    catalogError: "",
    pendingRecovery: false,
    primaryDisabled: true,
    primaryActionLabel: "保存，重新匹配岗位",
  },

  onLoad() {
    mountController(this);
    void loadMajorCatalog(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) {
      mountController(this);
      void loadMajorCatalog(this);
    }
  },

  onUnload() {
    runtimes.detach(this);
    catalogs.delete(this);
    partialSaveKeys.delete(this);
  },

  back() {
    navigateBackOr(routes.me);
  },

  retryLoad() {
    void runtimes.get(this)?.controller?.load();
  },

  retryCatalog() {
    void loadMajorCatalog(this);
  },

  inputMajor(event) {
    const major = event.detail.value.trimStart();
    runtimes.get(this)?.controller?.changeDraft({ major });
    this.setData({
      majorCategory: "",
      majorSuggestions: searchMajors(catalogs.get(this) ?? [], major),
    });
  },

  selectMajor(event) {
    const name = String(event.currentTarget.dataset.name ?? "");
    const category = String(event.currentTarget.dataset.category ?? "");
    if (!name) return;
    runtimes.get(this)?.controller?.changeDraft({ major: name });
    this.setData({ majorCategory: category, majorSuggestions: [] });
  },

  chooseEducation(event) {
    const education = educationOptions[Number(event.detail.value)];
    if (education) runtimes.get(this)?.controller?.changeDraft({ education });
  },

  chooseDegree(event) {
    const degree = degreeOptions[Number(event.detail.value)];
    if (degree) runtimes.get(this)?.controller?.changeDraft({ degree });
  },

  chooseGraduation(event) {
    const option = graduationOptions[Number(event.detail.value)];
    if (!option) return;
    runtimes.get(this)?.controller?.changeDraft({
      graduationYear: option.year,
      freshGraduate: option.freshGraduate,
    });
  },

  chooseGender(event) {
    const gender = String(event.currentTarget.dataset.value ?? "");
    if (qualificationGenderOptions.some((option) => option === gender)) {
      runtimes.get(this)?.controller?.changeDraft({ gender });
    }
  },

  choosePoliticalStatus(event) {
    const politicalStatus = String(event.currentTarget.dataset.value ?? "");
    if (qualificationPoliticalStatusOptions.some((option) => option === politicalStatus)) {
      runtimes.get(this)?.controller?.changeDraft({ politicalStatus });
    }
  },

  toggleCertification(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    const certifications = toggleQualificationCertification(this.data.certifications, value);
    runtimes.get(this)?.controller?.changeDraft({ certifications });
  },

  chooseServiceMode(event) {
    const mode = String(event.currentTarget.dataset.mode ?? "");
    if (mode === "none") {
      runtimes.get(this)?.controller?.changeDraft({ serviceProject: "无", grassrootsYears: 0 });
      this.setData({ serviceExpanded: false });
    } else if (mode === "has") {
      const reset = this.data.serviceProject === "无";
      runtimes.get(this)?.controller?.changeDraft({
        serviceProject: reset ? "" : this.data.serviceProject,
        grassrootsYears: reset ? -1 : this.data.grassrootsYears,
      });
      this.setData({ serviceExpanded: true });
    }
  },

  chooseServiceProject(event) {
    const serviceProject = String(event.currentTarget.dataset.value ?? "");
    if (serviceProjectOptions.includes(serviceProject)) {
      runtimes.get(this)?.controller?.changeDraft({ serviceProject });
    }
  },

  chooseWorkYears(event) {
    const workYears = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(workYears) && workYears >= 0) {
      runtimes.get(this)?.controller?.changeDraft({ workYears });
    }
  },

  chooseGrassrootsYears(event) {
    const grassrootsYears = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(grassrootsYears) && grassrootsYears >= 0) {
      runtimes.get(this)?.controller?.changeDraft({ grassrootsYears });
    }
  },

  inputHukou(event) {
    runtimes.get(this)?.controller?.changeDraft({ hukou: event.detail.value.trimStart() });
  },

  submit() {
    void saveCompleteProfile(this);
  },

  saveAndExit() {
    void savePartialProfile(this);
  },

  markLater() {
    void savePartialProfile(this);
  },
});

function mountController(page: QualificationProfilePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const client = new ApiClient(undefined, session.accessToken);
  const runtime = runtimes.attach(page);
  const controller = new QualificationProfileEditorController({
    userId: session.userId,
    profileApi: new QualificationApi(client),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData(pageState(state));
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  page.setData(pageState(controller.snapshot()));
  void controller.load();
}

function pageState(state: QualificationProfileEditorState): Partial<QualificationProfilePageData> {
  const draft = state.draft;
  return {
    ...draft,
    certificationOptions: certificationViews(draft.certifications),
    graduationLabel: graduationLabel(draft),
    serviceExpanded: Boolean(draft.serviceProject && draft.serviceProject !== "无"),
    error: state.validationError || state.errorMessage,
    requestId: state.requestId,
    saving: state.saving,
    profileVersion: state.profileVersion,
    loadingProfile: state.status === "idle" || state.status === "loading",
    loadFailed: state.status === "error",
    pendingRecovery: state.pendingRecovery,
    primaryDisabled:
      !state.pendingRecovery &&
      (state.status !== "ready" ||
        state.saving ||
        !draft.education ||
        !draft.degree ||
        draft.major.trim().length < 2 ||
        draft.graduationYear < 2000 ||
        !draft.freshGraduate ||
        !draft.gender ||
        !draft.politicalStatus ||
        draft.certifications.length === 0 ||
        !draft.serviceProject ||
        (draft.serviceProject !== "无" && draft.grassrootsYears < 0) ||
        draft.workYears < 0),
    primaryActionLabel: state.pendingRecovery ? "看看上次的结果" : "保存，重新匹配岗位",
  };
}

async function loadMajorCatalog(page: QualificationProfilePage): Promise<void> {
  const session = currentAuthSession();
  if (!session) return;
  page.setData({ catalogLoading: true, catalogError: "", majorSuggestions: [] });
  try {
    const response = await new QualificationApi(
      new ApiClient(undefined, session.accessToken),
    ).getMajorCatalog();
    if (!sessionStillOwns(page, session.userId, session.accessToken)) return;
    const catalog = flattenMajorCatalog(response.data);
    catalogs.set(page, catalog);
    page.setData({
      catalogLoading: false,
      catalogError: "",
      majorSuggestions: searchMajors(catalog, page.data.major),
    });
  } catch {
    if (!sessionStillOwns(page, session.userId, session.accessToken)) return;
    catalogs.delete(page);
    page.setData({
      catalogLoading: false,
      catalogError: "哎，专业列表没加载出来，再试一次就好。",
      majorSuggestions: [],
    });
  }
}

function sessionStillOwns(
  page: QualificationProfilePage,
  userId: string,
  accessToken: string,
): boolean {
  const current = currentAuthSession();
  return (
    current?.userId === userId &&
    current.accessToken === accessToken &&
    Boolean(runtimes.get(page)?.controller?.sessionIsCurrent())
  );
}

function flattenMajorCatalog(tree: MajorTreeResponse): MajorOption[] {
  return tree.categories.flatMap((category) =>
    category.subCategories.flatMap((subcategory) =>
      subcategory.majors.map((major) => ({
        code: major.code,
        name: major.name,
        category: `${category.name} · ${subcategory.name}`,
      })),
    ),
  );
}

function searchMajors(catalog: MajorOption[], query: string): MajorOption[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return [];
  return catalog
    .filter(
      (major) =>
        major.name.toLocaleLowerCase().includes(normalized) ||
        major.code.toLocaleLowerCase().includes(normalized),
    )
    .slice(0, 6);
}

function certificationViews(certifications: readonly string[]): CertificationOptionView[] {
  return qualificationCertificationOptions.map((option) => ({
    ...option,
    selected: certifications.includes(option.value),
  }));
}

function graduationLabel(draft: QualificationDraft): string {
  if (draft.graduationYear < 2000 || !draft.freshGraduate) return "";
  return `${String(draft.graduationYear)}（${
    draft.freshGraduate === "true" ? "应届身份" : "社会人员"
  }）`;
}

async function saveCompleteProfile(page: QualificationProfilePage): Promise<void> {
  const controller = runtimes.get(page)?.controller;
  if (!controller || page.data.catalogLoading || page.data.catalogError) return;
  await controller.submit();
  if (controller.snapshot().saved) navigateBackOr(routes.me);
}

async function savePartialProfile(page: QualificationProfilePage): Promise<void> {
  const session = currentAuthSession();
  if (!session || page.data.savingDraft || page.data.saving) return;
  const facts = qualificationFactsFromDraft(page.data);
  if (!facts.length) {
    navigateBackOr(routes.me);
    return;
  }
  page.setData({ savingDraft: true, error: "", requestId: "" });
  try {
    let commandKey = partialSaveKeys.get(page);
    if (!commandKey) {
      commandKey = await createIdempotencyKey("qualification-profile-draft");
      partialSaveKeys.set(page, commandKey);
    }
    await new QualificationApi(new ApiClient(undefined, session.accessToken)).updateProfile(
      facts,
      commandKey,
    );
    partialSaveKeys.delete(page);
    page.setData({ savingDraft: false });
    navigateBackOr(routes.me);
  } catch (error) {
    page.setData({
      savingDraft: false,
      error: error instanceof Error ? error.message : "哎，没保存上，别急，再点一次就好。",
    });
  }
}
