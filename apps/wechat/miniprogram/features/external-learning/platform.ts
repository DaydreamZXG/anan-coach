import type { LocalAnswerImageFile, LocalImportFile } from "./contracts";

export async function ensureCameraPermission(): Promise<"granted" | "denied"> {
  const setting = await wx.getSetting();
  if (setting.authSetting["scope.camera"] === true) return "granted";
  if (setting.authSetting["scope.camera"] === false) return "denied";
  try {
    await wx.authorize({ scope: "scope.camera" });
    return "granted";
  } catch {
    return "denied";
  }
}

export async function requestCameraSetting(): Promise<boolean> {
  const result = await wx.showModal({
    title: "需要相机权限",
    content: "相机仅用于拍摄你要导入的题目或答题卡，未确认的识别结果不会进入学习诊断。",
    confirmText: "去设置",
    cancelText: "暂不开启",
  });
  if (!result.confirm) return false;
  const setting = await wx.openSetting();
  return setting.authSetting["scope.camera"] === true;
}

export async function chooseQuestionImage(
  source: "camera" | "album",
): Promise<LocalImportFile | null> {
  try {
    const result = await wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: [source],
      sizeType: ["original"],
    });
    const file = result.tempFiles.at(0);
    if (!file) return null;
    const path = file.tempFilePath;
    return {
      localPath: path,
      displayName: fileName(path, source === "camera" ? "拍照题目.jpg" : "相册题目.jpg"),
      sourceKind: source === "camera" ? "camera_image" : "image_import",
      mimeType: imageMimeType(path),
      byteSize: file.size,
      pageCount: null,
    };
  } catch (error) {
    if (isUserCancellation(error)) return null;
    throw error;
  }
}

export async function chooseQuestionPdf(): Promise<LocalImportFile | null> {
  try {
    const result = await wx.chooseMessageFile({ count: 1, type: "file", extension: ["pdf"] });
    const file = result.tempFiles.at(0);
    if (!file) return null;
    return {
      localPath: file.path,
      displayName: file.name || fileName(file.path, "导入题目.pdf"),
      sourceKind: "pdf_upload",
      mimeType: "application/pdf",
      byteSize: file.size,
      pageCount: null,
    };
  } catch (error) {
    if (isUserCancellation(error)) return null;
    throw error;
  }
}

export async function chooseAnswerImage(
  source: "camera" | "album",
): Promise<LocalAnswerImageFile | null> {
  const file = await chooseQuestionImage(source);
  if (!file || file.mimeType === "application/pdf") return null;
  return {
    localPath: file.localPath,
    displayName: file.displayName,
    sourceKind: "answer_sheet",
    mimeType: file.mimeType,
    byteSize: file.byteSize,
  };
}

export async function openPacketFile(
  localFilePath: string,
  fileType: "pdf" | "note_app_file",
): Promise<void> {
  if (fileType === "note_app_file") {
    await wx.shareFileMessage({
      filePath: localFilePath,
      fileName: fileName(localFilePath, "学习包"),
    });
    return;
  }
  await wx.openDocument({ filePath: localFilePath, fileType: "pdf", showMenu: true });
}

function imageMimeType(path: string): "image/jpeg" | "image/png" {
  return path.toLowerCase().split("?", 1)[0]?.endsWith(".png") ? "image/png" : "image/jpeg";
}

function fileName(path: string, fallback: string): string {
  const name = path.split("?", 1)[0]?.split("/").at(-1)?.trim();
  return name || fallback;
}

function isUserCancellation(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const rawMessage = (error as { errMsg?: unknown }).errMsg;
  const message = typeof rawMessage === "string" ? rawMessage.toLowerCase() : "";
  return message.includes("cancel");
}
