import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  QualificationProfileEditorController,
  type QualificationProfileEditorState,
} from "../../../features/qualification/profile-editor-controller";
import {
  emptyQualificationDraft,
  type QualificationDraft,
} from "../../../features/qualification/onboarding";

const steps = [
  { label: "基础", title: "学历和专业", copy: "岗位表首先按学历、专业原文和大类规则筛选。" },
  { label: "身份", title: "身份与政治面貌", copy: "应届身份、党员等条件会直接影响可报范围。" },
  { label: "经历", title: "经历与户籍", copy: "普通工作经历、基层经历和服务基层项目分开保存。" },
  {
    label: "确认",
    title: "保存前本人确认",
    copy: "保存后生成新的资格档案版本，并触发岗位重新核验。",
  },
] as const;

interface QualificationProfilePageData extends QualificationDraft {
  current: number;
  steps: typeof steps;
  error: string;
  requestId: string;
  saving: boolean;
  saved: boolean;
  profileVersion: number;
  loadingProfile: boolean;
  loadFailed: boolean;
  pendingRecovery: boolean;
  primaryActionLabel: string;
}

interface QualificationProfilePageCustom {
  retryLoad(): void;
  chooseEducation(event: WechatMiniprogram.BaseEvent): void;
  inputMajor(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  chooseFreshGraduate(event: WechatMiniprogram.BaseEvent): void;
  choosePoliticalStatus(event: WechatMiniprogram.BaseEvent): void;
  chooseWorkYears(event: WechatMiniprogram.BaseEvent): void;
  chooseGrassrootsYears(event: WechatMiniprogram.BaseEvent): void;
  chooseServiceProject(event: WechatMiniprogram.BaseEvent): void;
  inputHukou(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  previous(): void;
  next(): void;
  editAgain(): void;
  openPositions(): void;
  openScoreSheet(): void;
}

type QualificationProfilePage = WechatMiniprogram.Page.Instance<
  QualificationProfilePageData,
  QualificationProfilePageCustom
>;

const runtimes = new PageControllerRegistry<
  QualificationProfilePage,
  QualificationProfileEditorController
>();

Page<QualificationProfilePageData, QualificationProfilePageCustom>({
  data: {
    ...emptyQualificationDraft,
    current: 0,
    steps,
    error: "",
    requestId: "",
    saving: false,
    saved: false,
    profileVersion: 0,
    loadingProfile: true,
    loadFailed: false,
    pendingRecovery: false,
    primaryActionLabel: "继续",
  },

  onLoad() {
    mountController(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) mountController(this);
  },

  onUnload() {
    runtimes.detach(this);
  },

  retryLoad() {
    void runtimes.get(this)?.controller?.load();
  },

  chooseEducation(event) {
    runtimes
      .get(this)
      ?.controller?.changeDraft({ education: String(event.currentTarget.dataset.value ?? "") });
  },

  inputMajor(event) {
    runtimes.get(this)?.controller?.changeDraft({ major: event.detail.value.trimStart() });
  },

  chooseFreshGraduate(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "true" || value === "false") {
      runtimes.get(this)?.controller?.changeDraft({ freshGraduate: value });
    }
  },

  choosePoliticalStatus(event) {
    runtimes.get(this)?.controller?.changeDraft({
      politicalStatus: String(event.currentTarget.dataset.value ?? ""),
    });
  },

  chooseWorkYears(event) {
    const value = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(value)) {
      runtimes.get(this)?.controller?.changeDraft({ workYears: value });
    }
  },

  chooseGrassrootsYears(event) {
    const value = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(value)) {
      runtimes.get(this)?.controller?.changeDraft({ grassrootsYears: value });
    }
  },

  chooseServiceProject(event) {
    runtimes.get(this)?.controller?.changeDraft({
      serviceProject: String(event.currentTarget.dataset.value ?? ""),
    });
  },

  inputHukou(event) {
    runtimes.get(this)?.controller?.changeDraft({ hukou: event.detail.value.trimStart() });
  },

  previous() {
    runtimes.get(this)?.controller?.previous();
  },

  next() {
    runtimes.get(this)?.controller?.next();
  },

  editAgain() {
    runtimes.get(this)?.controller?.editAgain();
  },

  openPositions() {
    void wx.switchTab({ url: routes.positions });
  },

  openScoreSheet() {
    void wx.switchTab({ url: routes.positions });
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

function pageState(state: QualificationProfileEditorState): QualificationProfilePageData {
  return {
    ...state.draft,
    current: state.current,
    steps,
    error: state.validationError || state.errorMessage,
    requestId: state.requestId,
    saving: state.saving,
    saved: state.saved,
    profileVersion: state.profileVersion,
    loadingProfile: state.status === "idle" || state.status === "loading",
    loadFailed: state.status === "error",
    pendingRecovery: state.pendingRecovery,
    primaryActionLabel: state.pendingRecovery
      ? "查询原保存结果"
      : state.current === 3
        ? "保存并重新核验岗位"
        : "继续",
  };
}
