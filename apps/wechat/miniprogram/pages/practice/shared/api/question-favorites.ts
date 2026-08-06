import type { ApiClient } from "../../../../api/client";
import type {
  QuestionFavoritePage,
  QuestionFavoriteReceipt,
  QuestionFavoriteState,
  SetQuestionFavoriteCommand,
} from "../../../../api/generated/types.gen";
import type { ApiEnvelope } from "../../../../domain/contracts";

const QUESTION_FAVORITES_PATH = "/api/v2/xingce/question-favorites";

export class QuestionFavoritesApi {
  constructor(private readonly client: ApiClient) {}

  getState(questionId: string): Promise<ApiEnvelope<QuestionFavoriteState>> {
    return this.client.get<QuestionFavoriteState>(
      `${QUESTION_FAVORITES_PATH}/${encodeURIComponent(questionId)}`,
    );
  }

  list(params?: {
    cursor?: string;
    pageSize?: number;
  }): Promise<ApiEnvelope<QuestionFavoritePage>> {
    const query: string[] = [];
    if (params?.cursor !== undefined) {
      query.push(`cursor=${encodeURIComponent(params.cursor)}`);
    }
    if (params?.pageSize !== undefined) {
      query.push(`pageSize=${String(params.pageSize)}`);
    }
    const queryString = query.length > 0 ? `?${query.join("&")}` : "";
    return this.client.get<QuestionFavoritePage>(`${QUESTION_FAVORITES_PATH}${queryString}`);
  }

  setState(
    questionId: string,
    command: SetQuestionFavoriteCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<QuestionFavoriteReceipt>> {
    return this.client.put<QuestionFavoriteReceipt>(
      `${QUESTION_FAVORITES_PATH}/${encodeURIComponent(questionId)}`,
      command,
      { idempotencyKey },
    );
  }
}
