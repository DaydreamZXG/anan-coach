import type { ApiEnvelope } from "../../../domain/contracts";
import type {
  AccountDeletionView,
  DataExportPayload,
  DataExportView,
  PrivacyOverview,
} from "../../../api/generated/types.gen";
import type { ApiClient } from "../../../api/client";

export class DataRightsApi {
  constructor(private readonly client: ApiClient) {}

  overview(): Promise<ApiEnvelope<PrivacyOverview>> {
    return this.client.get<PrivacyOverview>("/api/v2/data-rights/overview");
  }

  createExport(commandKey: string): Promise<ApiEnvelope<DataExportView>> {
    return this.client.post<DataExportView>("/api/v2/data-rights/exports", { commandKey });
  }

  exportStatus(exportId: string): Promise<ApiEnvelope<DataExportView>> {
    return this.client.get<DataExportView>(
      `/api/v2/data-rights/exports/${encodeURIComponent(exportId)}`,
    );
  }

  exportPayload(exportId: string): Promise<ApiEnvelope<DataExportPayload>> {
    return this.client.get<DataExportPayload>(
      `/api/v2/data-rights/exports/${encodeURIComponent(exportId)}/payload`,
    );
  }

  requestDeletion(
    commandKey: string,
    confirmationPhrase: string,
  ): Promise<ApiEnvelope<AccountDeletionView>> {
    return this.client.post<AccountDeletionView>("/api/v2/data-rights/deletions", {
      commandKey,
      confirmationPhrase,
    });
  }
}
