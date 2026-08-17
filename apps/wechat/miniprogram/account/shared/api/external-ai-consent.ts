import type { ApiClient } from "../../../api/client";
import type { ApiEnvelope } from "../../../domain/contracts";

export type ExternalAIConsentState = "not_accepted" | "accepted" | "withdrawn";

export interface ExternalAIProcessorDisclosure {
  processorReleaseId: string;
  displayName: string;
  operatorName: string | null;
  modelTrainingUse: string;
  retentionPolicy: string;
  deletionSupport: string;
}

export interface ExternalAIConsentView {
  contractVersion: "external-ai-consent-view@v1";
  policyVersion: string;
  state: ExternalAIConsentState;
  consentEventId: string | null;
  acceptedAt: string | null;
  withdrawnAt: string | null;
  title: string;
  summary: string;
  dataSent: string[];
  providerRisk: string;
  withdrawalEffect: string;
  processors: ExternalAIProcessorDisclosure[];
  generatedAt: string;
}

export class ExternalAIConsentApi {
  constructor(private readonly client: ApiClient) {}

  current(): Promise<ApiEnvelope<ExternalAIConsentView>> {
    return this.client.get<ExternalAIConsentView>("/api/v2/privacy/external-ai-consent");
  }

  accept(commandKey: string, surface: string): Promise<ApiEnvelope<ExternalAIConsentView>> {
    return this.client.post<ExternalAIConsentView>("/api/v2/privacy/external-ai-consent/accept", {
      commandKey,
      surface,
    });
  }

  withdraw(commandKey: string, surface: string): Promise<ApiEnvelope<ExternalAIConsentView>> {
    return this.client.post<ExternalAIConsentView>("/api/v2/privacy/external-ai-consent/withdraw", {
      commandKey,
      surface,
    });
  }
}

/** Deduplicates first-use reads, the single confirmation, and concurrent accept taps. */
export class ExternalAIConsentCoordinator {
  private currentView: ExternalAIConsentView | null = null;
  private currentRequest: Promise<ExternalAIConsentView> | null = null;
  private acceptance: Promise<boolean> | null = null;

  constructor(
    private readonly api: ExternalAIConsentApi,
    private readonly surface: string,
  ) {}

  prefetch(): void {
    void this.current().catch(() => undefined);
  }

  async current(): Promise<ExternalAIConsentView> {
    if (this.currentView) return this.currentView;
    this.currentRequest ??= this.api
      .current()
      .then((response) => {
        this.currentView = response.data;
        return response.data;
      })
      .finally(() => {
        this.currentRequest = null;
      });
    return this.currentRequest;
  }

  async ensureAccepted(
    confirmOnce: (view: ExternalAIConsentView) => Promise<boolean>,
    commandKey: () => Promise<string>,
  ): Promise<boolean> {
    if (this.acceptance) return this.acceptance;
    this.acceptance = this.ensureAcceptedOnce(confirmOnce, commandKey).finally(() => {
      this.acceptance = null;
    });
    return this.acceptance;
  }

  invalidate(): void {
    this.currentView = null;
    this.currentRequest = null;
  }

  private async ensureAcceptedOnce(
    confirmOnce: (view: ExternalAIConsentView) => Promise<boolean>,
    commandKey: () => Promise<string>,
  ): Promise<boolean> {
    const current = await this.current();
    if (current.state === "accepted") return true;
    if (!(await confirmOnce(current))) return false;
    const accepted = await this.api.accept(await commandKey(), this.surface);
    this.currentView = accepted.data;
    return accepted.data.state === "accepted";
  }
}
