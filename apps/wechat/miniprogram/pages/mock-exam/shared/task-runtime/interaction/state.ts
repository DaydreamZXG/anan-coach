export type TaskInteractionOverlay =
  "none" | "answer_sheet" | "draft" | "tools" | "help" | "companion" | "dispute" | "submit_confirm";

export type TaskAnnotationSurface = "none" | "question" | "material";
export type TaskInteractionLifecycle = "visible" | "hidden" | "unloaded";
export type TaskTouchOwner = "question" | "annotation" | "overlay" | "none";

export interface TaskInteractionState {
  overlay: TaskInteractionOverlay;
  annotationSurface: TaskAnnotationSurface;
  lifecycle: TaskInteractionLifecycle;
  locked: boolean;
}

export type TaskInteractionAction =
  | { type: "OVERLAY_OPENED"; overlay: Exclude<TaskInteractionOverlay, "none"> }
  | { type: "OVERLAY_TOGGLED"; overlay: Exclude<TaskInteractionOverlay, "none"> }
  | { type: "OVERLAY_CLOSED"; overlay?: Exclude<TaskInteractionOverlay, "none"> }
  | { type: "ANNOTATION_OPENED"; surface: Exclude<TaskAnnotationSurface, "none"> }
  | { type: "ANNOTATION_CLOSED" }
  | { type: "INTERACTIONS_CLOSED" }
  | { type: "QUESTION_CHANGED" }
  | { type: "PAGE_SHOWN" }
  | { type: "PAGE_HIDDEN" }
  | { type: "PAGE_UNLOADED" }
  | { type: "ACCOUNT_CHANGED" }
  | { type: "SESSION_LOADING" }
  | { type: "SESSION_SUSPENDED" }
  | { type: "SESSION_READY" }
  | { type: "SESSION_TERMINAL" }
  | { type: "SUBMIT_STARTED" }
  | { type: "SUBMIT_FAILED" }
  | { type: "LOCK_CHANGED"; locked: boolean };

export interface TaskInteractionProjection {
  overlay: TaskInteractionOverlay;
  annotationOpen: boolean;
  annotationSurface: "" | Exclude<TaskAnnotationSurface, "none">;
  annotationMode: "annotate" | "static";
  touchOwner: TaskTouchOwner;
  swipeBlocked: boolean;
  interactionLocked: boolean;
  answerSheetOpen: boolean;
  draftOpen: boolean;
  toolsOpen: boolean;
  helpOpen: boolean;
  ananContextOpen: boolean;
  disputeOpen: boolean;
  submitConfirmOpen: boolean;
}

const closedSurface = {
  overlay: "none" as const,
  annotationSurface: "none" as const,
};

export function createTaskInteractionState(
  initial: Partial<TaskInteractionState> = {},
): TaskInteractionState {
  return {
    overlay: initial.overlay ?? "none",
    annotationSurface: initial.annotationSurface ?? "none",
    lifecycle: initial.lifecycle ?? "visible",
    locked: initial.locked ?? false,
  };
}

export function reduceTaskInteraction(
  state: TaskInteractionState,
  action: TaskInteractionAction,
): TaskInteractionState {
  switch (action.type) {
    case "OVERLAY_OPENED":
      if (!canOpenInteraction(state)) return state;
      return { ...state, overlay: action.overlay, annotationSurface: "none" };
    case "OVERLAY_TOGGLED":
      if (!canOpenInteraction(state)) return state;
      return state.overlay === action.overlay
        ? { ...state, ...closedSurface }
        : { ...state, overlay: action.overlay, annotationSurface: "none" };
    case "OVERLAY_CLOSED":
      if (action.overlay && state.overlay !== action.overlay) return state;
      return state.overlay === "none" ? state : { ...state, overlay: "none" };
    case "ANNOTATION_OPENED":
      if (!canOpenInteraction(state)) return state;
      return { ...state, overlay: "none", annotationSurface: action.surface };
    case "ANNOTATION_CLOSED":
      return state.annotationSurface === "none" ? state : { ...state, annotationSurface: "none" };
    case "INTERACTIONS_CLOSED":
    case "QUESTION_CHANGED":
      return closeSurface(state);
    case "PAGE_SHOWN":
      return state.lifecycle === "unloaded"
        ? state
        : { ...closeSurface(state), lifecycle: "visible" };
    case "PAGE_HIDDEN":
      return { ...closeSurface(state), lifecycle: "hidden" };
    case "PAGE_UNLOADED":
      return { ...closeSurface(state), lifecycle: "unloaded", locked: true };
    case "ACCOUNT_CHANGED":
    case "SESSION_LOADING":
    case "SESSION_SUSPENDED":
    case "SUBMIT_STARTED":
    case "SESSION_TERMINAL":
      return { ...closeSurface(state), locked: true };
    case "SESSION_READY":
    case "SUBMIT_FAILED":
      return state.lifecycle === "unloaded" ? state : { ...closeSurface(state), locked: false };
    case "LOCK_CHANGED":
      return { ...state, locked: action.locked };
  }
}

export function projectTaskInteraction(state: TaskInteractionState): TaskInteractionProjection {
  const interactionLocked = state.locked || state.lifecycle !== "visible";
  const visible = state.lifecycle === "visible";
  const annotationSurface =
    visible && state.annotationSurface !== "none" ? state.annotationSurface : "";
  const annotationOpen = annotationSurface !== "";
  const overlay = visible ? state.overlay : "none";
  const touchOwner: TaskTouchOwner = !visible
    ? "none"
    : annotationOpen
      ? "annotation"
      : overlay !== "none"
        ? "overlay"
        : state.locked
          ? "none"
          : "question";

  return {
    overlay,
    annotationOpen,
    annotationSurface,
    annotationMode: annotationOpen ? "annotate" : "static",
    touchOwner,
    swipeBlocked: touchOwner !== "question",
    interactionLocked,
    answerSheetOpen: overlay === "answer_sheet",
    draftOpen: overlay === "draft",
    toolsOpen: overlay === "tools",
    helpOpen: overlay === "help",
    ananContextOpen: overlay === "companion",
    disputeOpen: overlay === "dispute",
    submitConfirmOpen: overlay === "submit_confirm",
  };
}

function canOpenInteraction(state: TaskInteractionState): boolean {
  return state.lifecycle === "visible" && !state.locked;
}

function closeSurface(state: TaskInteractionState): TaskInteractionState {
  if (state.overlay === "none" && state.annotationSurface === "none") return state;
  return { ...state, ...closedSurface };
}
