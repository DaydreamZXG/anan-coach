import type { ApiClient } from "../../../../api/client";
import type {
  SpeechToTextRequest,
  SpeechToTextResponse,
  TextToSpeechResponse as GeneratedTextToSpeechResponse,
} from "../../../../api/generated/types.gen";

type SpeechToTextCommand = Pick<SpeechToTextRequest, "audioBase64" | "durationMilliseconds">;
type TextToSpeechResponse = Omit<GeneratedTextToSpeechResponse, "mimeType"> & {
  mimeType: "audio/mpeg";
};

export class XfyunApi {
  constructor(private readonly client: ApiClient) {}

  async speechToText(command: SpeechToTextCommand): Promise<SpeechToTextResponse> {
    const response = await this.client.post<SpeechToTextResponse>(
      "/api/v2/xfyun/speech-to-text",
      { ...command, encoding: "lame" },
      { timeoutMs: 45_000 },
    );
    return response.data;
  }

  async textToSpeech(text: string): Promise<TextToSpeechResponse> {
    const response = await this.client.post<GeneratedTextToSpeechResponse>(
      "/api/v2/xfyun/text-to-speech",
      { text },
      { timeoutMs: 45_000 },
    );
    return { ...response.data, mimeType: response.data.mimeType ?? "audio/mpeg" };
  }
}
