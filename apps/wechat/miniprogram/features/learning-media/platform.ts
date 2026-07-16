import { sha256Hex } from "../../api/sha256";
import type { InspectedVoiceFile } from "./contracts";

export interface RecordedVoiceFile {
  tempFilePath: string;
  byteSize: number;
  durationMilliseconds: number;
  mimeType: "audio/mpeg";
  extension: "mp3";
}

interface ActiveRecorderOwner {
  token: symbol;
  cancelled: boolean;
  onStopped(result: RecordedVoiceFile): void;
  onError(error: Error): void;
}

interface RecorderBroker {
  manager: WechatMiniprogram.RecorderManager;
  active: ActiveRecorderOwner | null;
}

let recorderBroker: RecorderBroker | null = null;

export async function ensureRecordPermission(): Promise<"granted" | "denied"> {
  const setting = await wx.getSetting();
  if (setting.authSetting["scope.record"] === true) return "granted";
  if (setting.authSetting["scope.record"] === false) return "denied";
  try {
    await wx.authorize({ scope: "scope.record" });
    return "granted";
  } catch {
    return "denied";
  }
}

export async function requestRecordSetting(): Promise<boolean> {
  const result = await wx.showModal({
    title: "需要麦克风权限",
    content: "麦克风仅用于录制这次复盘。岸岸会在你确认后上传，并等待安全检查完成。",
    confirmText: "去设置",
    cancelText: "暂不开启",
  });
  if (!result.confirm) return false;
  const setting = await wx.openSetting();
  return setting.authSetting["scope.record"] === true;
}

export class WechatVoiceRecorder {
  private readonly token = Symbol("reflection-voice-recorder");
  private disposed = false;

  start(onStopped: (result: RecordedVoiceFile) => void, onError: (error: Error) => void): void {
    if (this.disposed) throw new Error("录音器已关闭。");
    const broker = getRecorderBroker();
    if (broker.active) throw new Error("另一段录音正在进行，请先结束。");
    const owner: ActiveRecorderOwner = {
      token: this.token,
      cancelled: false,
      onStopped,
      onError,
    };
    broker.active = owner;
    try {
      broker.manager.start({
        duration: 180_000,
        sampleRate: 16_000,
        numberOfChannels: 1,
        encodeBitRate: 48_000,
        format: "mp3",
        audioSource: "auto",
      });
    } catch (error) {
      broker.active = null;
      throw asError(error, "无法启动录音。");
    }
  }

  stop(): void {
    const broker = recorderBroker;
    if (broker?.active?.token === this.token) broker.manager.stop();
  }

  dispose(): void {
    this.disposed = true;
    const broker = recorderBroker;
    if (broker?.active?.token !== this.token) return;
    broker.active.cancelled = true;
    broker.manager.stop();
  }
}

export async function persistRecordedVoice(tempFilePath: string): Promise<string> {
  const fileSystem = wx.getFileSystemManager();
  return new Promise((resolve, reject) => {
    fileSystem.saveFile({
      tempFilePath,
      success: (result) => {
        resolve(result.savedFilePath);
      },
      fail: () => {
        reject(new Error("录音草稿未能保存到本机。"));
      },
    });
  });
}

export async function inspectVoiceFile(localPath: string): Promise<InspectedVoiceFile> {
  const fileSystem = wx.getFileSystemManager();
  const metadata = await new Promise<WechatMiniprogram.GetFileInfoSuccessCallbackResult>(
    (resolve, reject) => {
      fileSystem.getFileInfo({
        filePath: localPath,
        digestAlgorithm: "sha256",
        success: (result) => {
          resolve(result);
        },
        fail: () => {
          reject(new Error("本地录音草稿已不可读。"));
        },
      });
    },
  );
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    fileSystem.readFile({
      filePath: localPath,
      success: (result) => {
        if (result.data instanceof ArrayBuffer) resolve(result.data);
        else reject(new Error("本地录音返回了非二进制内容。"));
      },
      fail: () => {
        reject(new Error("本地录音草稿已不可读。"));
      },
    });
  });
  if (bytes.byteLength !== metadata.size) {
    throw new Error("录音文件大小在上传前发生了变化。");
  }
  const contentSha256 = sha256Hex(bytes);
  if (metadata.digest.toLowerCase() !== contentSha256) {
    throw new Error("录音 SHA-256 完整性校验失败。");
  }
  return { byteSize: metadata.size, sha256: contentSha256, bytes };
}

export function deleteLocalVoice(localPath: string): Promise<void> {
  const fileSystem = wx.getFileSystemManager();
  return new Promise((resolve, reject) => {
    fileSystem.unlink({
      filePath: localPath,
      success: () => {
        resolve();
      },
      fail: () => {
        reject(new Error("本地录音草稿删除失败。"));
      },
    });
  });
}

export class WechatVoicePreview {
  private audio: WechatMiniprogram.InnerAudioContext | null = null;
  private disposed = false;
  private currentPath = "";
  private onEnded: (() => void) | null = null;
  private onError: ((error: Error) => void) | null = null;

  play(localPath: string, onEnded: () => void, onError: (error: Error) => void): void {
    if (this.disposed) throw new Error("试听器已关闭。");
    const audio = this.audio ?? this.createAudio();
    if (this.currentPath === localPath && !audio.paused) {
      audio.stop();
      this.currentPath = "";
      this.onEnded = null;
      this.onError = null;
      onEnded();
      return;
    }
    audio.stop();
    this.currentPath = localPath;
    this.onEnded = onEnded;
    this.onError = onError;
    audio.src = localPath;
    audio.play();
  }

  stop(): void {
    this.audio?.stop();
    this.currentPath = "";
    this.onEnded = null;
    this.onError = null;
  }

  dispose(): void {
    this.disposed = true;
    this.audio?.destroy();
    this.audio = null;
    this.currentPath = "";
    this.onEnded = null;
    this.onError = null;
  }

  private createAudio(): WechatMiniprogram.InnerAudioContext {
    const audio = wx.createInnerAudioContext();
    audio.autoplay = false;
    audio.onEnded(() => {
      this.currentPath = "";
      const callback = this.onEnded;
      this.onEnded = null;
      this.onError = null;
      callback?.();
    });
    audio.onError(() => {
      this.currentPath = "";
      const callback = this.onError;
      this.onEnded = null;
      this.onError = null;
      callback?.(new Error("这段录音暂时无法试听。"));
    });
    this.audio = audio;
    return audio;
  }
}

function getRecorderBroker(): RecorderBroker {
  if (recorderBroker) return recorderBroker;
  const manager = wx.getRecorderManager();
  const broker: RecorderBroker = { manager, active: null };
  manager.onStop((result) => {
    const owner = broker.active;
    broker.active = null;
    if (!owner || owner.cancelled) return;
    owner.onStopped({
      tempFilePath: result.tempFilePath,
      byteSize: result.fileSize,
      durationMilliseconds: result.duration,
      mimeType: "audio/mpeg",
      extension: "mp3",
    });
  });
  manager.onError(() => {
    const owner = broker.active;
    broker.active = null;
    if (!owner || owner.cancelled) return;
    owner.onError(new Error("录音被系统中断，请重新录制。"));
  });
  recorderBroker = broker;
  return broker;
}

function asError(value: unknown, fallback: string): Error {
  return value instanceof Error ? value : new Error(fallback);
}
