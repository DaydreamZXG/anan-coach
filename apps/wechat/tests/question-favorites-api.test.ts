import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import type { SetQuestionFavoriteCommand } from "../miniprogram/api/generated/types.gen";
import { QuestionFavoritesApi } from "../miniprogram/api/question-favorites";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("QuestionFavoritesApi", () => {
  it("loads one state through the encoded canonical question ID", async () => {
    const request = installRequest((options) => {
      expect(options.url).toBe(
        "https://api.example.test/api/v2/xingce/question-favorites/question%2F%E4%B8%80%20%3F%23",
      );
      expect(options.method).toBe("GET");
      expect(options.data).toBeUndefined();
      options.success?.(
        success(
          {
            availability: "available",
            contentChanged: false,
            favoriteState: "not_favorited",
            questionId: "question/一 ?#",
            revision: 0,
          },
          "favorite-state-1",
        ),
      );
    });
    const api = new QuestionFavoritesApi(new ApiClient("https://api.example.test", "token"));

    const response = await api.getState("question/一 ?#");

    expect(response.data.questionId).toBe("question/一 ?#");
    expect(request).toHaveBeenCalledOnce();
  });

  it("lists the first page without adding an empty query string", async () => {
    const request = installRequest((options) => {
      expect(options.url).toBe("https://api.example.test/api/v2/xingce/question-favorites");
      expect(options.method).toBe("GET");
      expect(options.data).toBeUndefined();
      options.success?.(
        success(
          {
            generatedAt: "2026-07-28T12:00:00Z",
            items: [],
            nextCursor: null,
          },
          "favorite-page-1",
        ),
      );
    });
    const api = new QuestionFavoritesApi(new ApiClient("https://api.example.test", "token"));

    const response = await api.list();

    expect(response.data.items).toEqual([]);
    expect(request).toHaveBeenCalledOnce();
  });

  it("lists with only the encoded cursor and pageSize query parameters", async () => {
    const request = installRequest((options) => {
      expect(options.url).toBe(
        "https://api.example.test/api/v2/xingce/question-favorites?cursor=next%2F%2B%3F%26%3D%E9%A1%B5&pageSize=20",
      );
      expect(options.method).toBe("GET");
      options.success?.(
        success(
          {
            generatedAt: "2026-07-28T12:00:00Z",
            items: [],
            nextCursor: null,
          },
          "favorite-page-2",
        ),
      );
    });
    const api = new QuestionFavoritesApi(new ApiClient("https://api.example.test", "token"));

    await api.list({ cursor: "next/+?&=页", pageSize: 20 });

    expect(request).toHaveBeenCalledOnce();
  });

  it("sets the desired state with the original command and caller-owned idempotency key", async () => {
    const command: SetQuestionFavoriteCommand = {
      desiredState: "favorited",
      contentVersionId: "content/v1",
      sourceSessionId: "session/1",
    };
    const request = installRequest((options) => {
      expect(options.url).toBe(
        "https://api.example.test/api/v2/xingce/question-favorites/q%2F%E5%90%AB%20%E7%A9%BA%E6%A0%BC%3F",
      );
      expect(options.method).toBe("PUT");
      expect(options.data).toBe(command);
      expect(options.header).toMatchObject({
        Authorization: "Bearer token",
        "Idempotency-Key": "favorite-command-1",
      });
      options.success?.(
        success(
          {
            favoriteState: "favorited",
            favoritedAt: "2026-07-28T12:00:00Z",
            questionId: "q/含 空格?",
            revision: 1,
            savedContentVersionId: "content/v1",
            updatedAt: "2026-07-28T12:00:00Z",
          },
          "favorite-command-1",
        ),
      );
    });
    const api = new QuestionFavoritesApi(new ApiClient("https://api.example.test", "token"));

    const response = await api.setState("q/含 空格?", command, "favorite-command-1");

    expect(response.data.favoriteState).toBe("favorited");
    expect(request).toHaveBeenCalledOnce();
  });

  it("passes the client error through without replacing or swallowing it", async () => {
    const failure = new Error("client transport failure");
    const client = new ApiClient("https://api.example.test", "token");
    vi.spyOn(client, "get").mockRejectedValue(failure);
    const api = new QuestionFavoritesApi(client);

    await expect(api.getState("question-1")).rejects.toBe(failure);
  });
});

function installRequest(
  handler: (options: WechatMiniprogram.RequestOption) => void,
): ReturnType<typeof vi.fn> {
  const request = vi.fn(handler);
  vi.stubGlobal("wx", { request });
  return request;
}

function success(data: unknown, requestId: string): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId,
      generatedAt: "2026-07-28T12:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}
