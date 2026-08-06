import type {
  QuestionFavoriteListItem,
  QuestionFavoriteState,
} from "../../../../../api/generated/types.gen";

export type FavoriteAvailabilityLabel = "当前版本" | "题目已更新" | "内容已下架";

export interface QuestionFavoriteItemView {
  questionId: string;
  moduleLabel: string;
  stem: string;
  availabilityLabel: FavoriteAvailabilityLabel;
  availabilityTone: "current" | "updated" | "unavailable";
  availabilityDescription: string;
  unavailable: boolean;
  contentChanged: boolean;
  favoritedAtLabel: string;
  mutationStatus: "idle" | "saving" | "unknown" | "error";
  mutationMessage: string;
  mutationRequestId: string;
}

export function presentQuestionFavoriteItem(
  item: QuestionFavoriteListItem,
): QuestionFavoriteItemView {
  const unavailable = item.availability === "unavailable";
  const contentChanged = !unavailable && item.contentChanged;
  const availabilityLabel: FavoriteAvailabilityLabel = unavailable
    ? "内容已下架"
    : contentChanged
      ? "题目已更新"
      : "当前版本";
  return {
    questionId: item.questionId,
    moduleLabel: unavailable ? "行测收藏" : item.preview?.moduleName?.trim() || "行测题目",
    stem: unavailable ? "" : item.preview?.stem.trim() || "题目摘要暂不可用，请刷新后再看。",
    availabilityLabel,
    availabilityTone: unavailable ? "unavailable" : contentChanged ? "updated" : "current",
    availabilityDescription: unavailable
      ? "只保留收藏记录，不展示题干、选项或作答入口；你仍可取消收藏。"
      : contentChanged
        ? "这里展示当前版本，与你收藏时的内容可能不同。"
        : "当前题目与收藏时版本一致。",
    unavailable,
    contentChanged,
    favoritedAtLabel: favoriteTimeLabel(item.favoritedAt),
    mutationStatus: "idle",
    mutationMessage: "",
    mutationRequestId: "",
  };
}

export function favoriteStateIsActive(state: QuestionFavoriteState): boolean {
  return state.favoriteState === "favorited";
}

function favoriteTimeLabel(value: string | null | undefined): string {
  if (!value) return "已收藏";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "已收藏";
  const date = new Date(timestamp);
  return `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")} 收藏`;
}
