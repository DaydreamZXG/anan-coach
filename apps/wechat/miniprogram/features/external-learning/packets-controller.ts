import type { LearningTrack, OfflinePacketVersion } from "../../api/external-learning-contracts";
import { createIdempotencyKey } from "../../platform/command-id";
import type {
  CreatePacketCommand,
  ExternalLearningGateway,
  PacketContentCandidate,
} from "./contracts";
import { externalLearningError, type ExternalLearningError } from "./error";
import { packetCardView, type PacketCardView } from "./presentation";

export interface LearningPacketsState {
  status: "idle" | "loading" | "ready" | "empty" | "error";
  packets: PacketCardView[];
  eligibleContent: SelectablePacketContentCandidate[];
  selectedContentVersionIds: string[];
  track: LearningTrack;
  renderFormat: "pdf" | "note_app_file";
  creating: boolean;
  pendingRecovery: boolean;
  operatingPacketId: string;
  openingPacketVersionId: string;
  partialReasons: string[];
  generatedAt: string;
  requestId: string;
  validationError: string;
  refreshError: ExternalLearningError | null;
  error: ExternalLearningError | null;
}

export interface SelectablePacketContentCandidate extends PacketContentCandidate {
  selected: boolean;
}

export interface LearningPacketsControllerOptions {
  gateway: Pick<
    ExternalLearningGateway,
    "listPackets" | "createPacket" | "retryPacket" | "renewPacket" | "getPacketAccess"
  >;
  onChange: (state: LearningPacketsState) => void;
  onOpenFile: (localFilePath: string, fileType: "pdf" | "note_app_file") => Promise<void>;
  createCommandKey?: (prefix: string) => Promise<string>;
}

interface PendingCreate {
  command: CreatePacketCommand;
  idempotencyKey: string;
}

interface PendingPacketOperation {
  kind: "retry" | "renew";
  packetId: string;
  packetVersionId: string;
  expectedVersionNo: number;
  idempotencyKey: string;
}

export class LearningPacketsController {
  private readonly gateway: LearningPacketsControllerOptions["gateway"];
  private readonly onChange: LearningPacketsControllerOptions["onChange"];
  private readonly onOpenFile: LearningPacketsControllerOptions["onOpenFile"];
  private readonly createCommandKey: (prefix: string) => Promise<string>;
  private state: LearningPacketsState;
  private disposed = false;
  private operationEpoch = 0;
  private commandPreparing = false;
  private pendingCreate: PendingCreate | null = null;
  private pendingPacketOperation: PendingPacketOperation | null = null;

  constructor(options: LearningPacketsControllerOptions) {
    this.gateway = options.gateway;
    this.onChange = options.onChange;
    this.onOpenFile = options.onOpenFile;
    this.createCommandKey = options.createCommandKey ?? createIdempotencyKey;
    this.state = {
      status: "idle",
      packets: [],
      eligibleContent: [],
      selectedContentVersionIds: [],
      track: "xingce",
      renderFormat: "pdf",
      creating: false,
      pendingRecovery: false,
      operatingPacketId: "",
      openingPacketVersionId: "",
      partialReasons: [],
      generatedAt: "",
      requestId: "",
      validationError: "",
      refreshError: null,
      error: null,
    };
  }

  snapshot(): LearningPacketsState {
    return cloneState(this.state);
  }

  async load(): Promise<void> {
    if (!this.isActive()) return;
    const epoch = ++this.operationEpoch;
    const retained = this.state.packets.length > 0 || this.state.eligibleContent.length > 0;
    this.patch({
      status: retained ? this.state.status : "loading",
      refreshError: null,
      error: null,
    });
    try {
      const result = await this.gateway.listPackets();
      if (!this.isCurrent(epoch)) return;
      const candidates = result.eligibleContent.map((item) => ({ ...item }));
      const allowedIds = new Set(candidates.map((item) => item.contentVersionId));
      const retainedSelection = this.state.selectedContentVersionIds.filter((id) =>
        allowedIds.has(id),
      );
      const selectedContentVersionIds =
        retainedSelection.length > 0
          ? retainedSelection
          : candidates
              .filter((item) => item.track === this.state.track && item.selectedByDefault)
              .map((item) => item.contentVersionId);
      const selectedIds = new Set(selectedContentVersionIds);
      const eligibleContent = candidates.map((item) => ({
        ...item,
        selected: selectedIds.has(item.contentVersionId),
      }));
      this.state = {
        ...this.state,
        status: result.packets.length || eligibleContent.length ? "ready" : "empty",
        packets: result.packets.map(packetCardView),
        eligibleContent,
        selectedContentVersionIds,
        partialReasons: [...result.partialReasons],
        generatedAt: result.generatedAt,
        requestId: result.requestId,
        refreshError: null,
        error: null,
      };
      this.publish();
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (retained) this.patch({ refreshError: view });
      else this.patch({ status: "error", error: view });
    }
  }

  setTrack(track: LearningTrack): void {
    if (!this.canEdit()) return;
    const selectedContentVersionIds = this.state.eligibleContent
      .filter((item) => item.track === track && item.selectedByDefault)
      .map((item) => item.contentVersionId);
    const selected = new Set(selectedContentVersionIds);
    this.patch({
      track,
      selectedContentVersionIds,
      eligibleContent: this.state.eligibleContent.map((item) => ({
        ...item,
        selected: selected.has(item.contentVersionId),
      })),
      validationError: "",
    });
  }

  setRenderFormat(renderFormat: "pdf" | "note_app_file"): void {
    if (this.canEdit()) this.patch({ renderFormat, validationError: "" });
  }

  toggleContent(contentVersionId: string): void {
    if (!this.canEdit()) return;
    const candidate = this.state.eligibleContent.find(
      (item) => item.contentVersionId === contentVersionId && item.track === this.state.track,
    );
    if (!candidate) return;
    const selected = this.state.selectedContentVersionIds.includes(contentVersionId);
    const selectedContentVersionIds = selected
      ? this.state.selectedContentVersionIds.filter((id) => id !== contentVersionId)
      : [...this.state.selectedContentVersionIds, contentVersionId];
    const selectedIds = new Set(selectedContentVersionIds);
    this.patch({
      selectedContentVersionIds,
      eligibleContent: this.state.eligibleContent.map((item) => ({
        ...item,
        selected: selectedIds.has(item.contentVersionId),
      })),
      validationError: "",
    });
  }

  async create(): Promise<void> {
    if (!this.canEdit()) return;
    if (this.state.selectedContentVersionIds.length === 0) {
      this.patch({ validationError: "至少选择 1 道已确认题目。" });
      return;
    }
    this.commandPreparing = true;
    let pending: PendingCreate;
    try {
      pending =
        this.pendingCreate ??
        (await this.newPendingCreate({
          renderFormat: this.state.renderFormat,
          items: this.selectedPacketItems(),
          expiresInDays: 14,
        }));
    } catch (error) {
      this.patch({ error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.commandPreparing = false;
    }
    if (!this.isActive()) return;
    this.pendingCreate = pending;
    const epoch = ++this.operationEpoch;
    this.patch({
      creating: true,
      pendingRecovery: false,
      error: null,
      validationError: "",
    });
    try {
      const packet = await this.gateway.createPacket(pending.command, pending.idempotencyKey);
      if (!this.isCurrent(epoch)) return;
      this.pendingCreate = null;
      this.upsertPacket(packet);
      this.patch({ creating: false, pendingRecovery: false, validationError: "" });
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingCreate = null;
      this.patch({ creating: false, pendingRecovery: view.uncertain, error: view });
    }
  }

  async operate(packetVersionId: string, kind: "retry" | "renew"): Promise<void> {
    if (!this.canEdit()) return;
    const packet = this.state.packets.find((item) => item.packetVersionId === packetVersionId);
    if (!packet || (kind === "retry" ? !packet.canRetry : !packet.canRenew)) return;
    this.commandPreparing = true;
    let pending: PendingPacketOperation;
    try {
      pending =
        this.pendingPacketOperation?.packetVersionId === packetVersionId &&
        this.pendingPacketOperation.kind === kind
          ? this.pendingPacketOperation
          : {
              kind,
              packetId: packet.packetId,
              packetVersionId: packet.packetVersionId,
              expectedVersionNo: packet.versionNo,
              idempotencyKey: await this.createCommandKey(`external-packet-${kind}`),
            };
    } catch (error) {
      this.patch({ error: externalLearningError(error), pendingRecovery: false });
      return;
    } finally {
      this.commandPreparing = false;
    }
    if (!this.isActive()) return;
    this.pendingPacketOperation = pending;
    const epoch = ++this.operationEpoch;
    this.patch({ operatingPacketId: packet.packetId, pendingRecovery: false, error: null });
    try {
      const result =
        kind === "retry"
          ? await this.gateway.retryPacket(
              {
                packetId: pending.packetId,
                failedPacketVersionId: pending.packetVersionId,
                expectedVersionNo: pending.expectedVersionNo,
              },
              pending.idempotencyKey,
            )
          : await this.gateway.renewPacket(
              {
                packetId: pending.packetId,
                expiredPacketVersionId: pending.packetVersionId,
                expectedVersionNo: pending.expectedVersionNo,
              },
              pending.idempotencyKey,
            );
      if (!this.isCurrent(epoch)) return;
      this.pendingPacketOperation = null;
      this.upsertPacket(result);
      this.patch({ operatingPacketId: "", pendingRecovery: false });
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      const view = externalLearningError(error);
      if (!view.uncertain) this.pendingPacketOperation = null;
      this.patch({ operatingPacketId: "", pendingRecovery: view.uncertain, error: view });
    }
  }

  async open(packetVersionId: string): Promise<void> {
    const packet = this.state.packets.find((item) => item.packetVersionId === packetVersionId);
    if (!packet?.canOpen || this.state.openingPacketVersionId || !this.isActive()) return;
    const epoch = ++this.operationEpoch;
    this.patch({ openingPacketVersionId: packetVersionId, error: null });
    try {
      const grant = await this.gateway.getPacketAccess(packetVersionId);
      if (!this.isCurrent(epoch)) return;
      await this.onOpenFile(grant.localFilePath, grant.fileType);
      if (this.isCurrent(epoch)) this.patch({ openingPacketVersionId: "" });
    } catch (error) {
      if (!this.isCurrent(epoch)) return;
      this.patch({ openingPacketVersionId: "", error: externalLearningError(error) });
    }
  }

  dispose(): void {
    this.disposed = true;
    this.commandPreparing = false;
    this.operationEpoch += 1;
  }

  private async newPendingCreate(command: CreatePacketCommand): Promise<PendingCreate> {
    return {
      command,
      idempotencyKey: await this.createCommandKey("external-packet-create"),
    };
  }

  private selectedPacketItems(): CreatePacketCommand["items"] {
    const selected = new Set(this.state.selectedContentVersionIds);
    return this.state.eligibleContent
      .filter((item) => item.track === this.state.track && selected.has(item.contentVersionId))
      .map((item) => ({
        questionId: item.questionId,
        contentVersionId: item.contentVersionId,
        contentLicenseId: item.contentLicenseId,
        expectedAuthorizationEpoch: item.expectedAuthorizationEpoch,
        track: item.track,
        components: [...item.components],
      }));
  }

  private upsertPacket(packet: OfflinePacketVersion): void {
    const packets = this.state.packets.filter(
      (item) => item.packetVersionId !== packet.packetVersionId,
    );
    packets.unshift(packetCardView(packet));
    this.state = { ...this.state, status: "ready", packets };
    this.publish();
  }

  private canEdit(): boolean {
    return (
      this.isActive() &&
      !this.commandPreparing &&
      !this.state.creating &&
      !this.state.operatingPacketId &&
      !this.state.openingPacketVersionId
    );
  }

  private patch(patch: Partial<LearningPacketsState>): void {
    this.state = { ...this.state, ...patch };
    this.publish();
  }

  private publish(): void {
    if (this.isActive()) this.onChange(this.snapshot());
  }

  private isActive(): boolean {
    return !this.disposed;
  }

  private isCurrent(epoch: number): boolean {
    return this.isActive() && this.operationEpoch === epoch;
  }
}

function cloneState(state: LearningPacketsState): LearningPacketsState {
  return {
    ...state,
    packets: state.packets.map((packet) => ({
      ...packet,
      items: packet.items.map((item) => ({ ...item })),
      licenseSnapshotRefs: [...packet.licenseSnapshotRefs],
    })),
    eligibleContent: state.eligibleContent.map((item) => ({ ...item })),
    selectedContentVersionIds: [...state.selectedContentVersionIds],
    partialReasons: [...state.partialReasons],
    refreshError: state.refreshError ? { ...state.refreshError } : null,
    error: state.error ? { ...state.error } : null,
  };
}
