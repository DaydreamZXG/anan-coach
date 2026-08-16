import type {
  LocalAnswerImageFile,
  LocalImportFile,
} from "../../../../features/external-learning/contracts";
import {
  captureSourcePhoto,
  chooseSourceImage,
  ensureCameraPermission,
  requestCameraSetting,
} from "../../platform/source-image";

export { ensureCameraPermission, requestCameraSetting };

export async function chooseQuestionImage(
  source: "camera" | "album",
): Promise<LocalImportFile | null> {
  return chooseSourceImage(source, source === "camera" ? "拍照题目.jpg" : "相册题目.jpg");
}

export async function takeQuestionPhoto(): Promise<LocalImportFile> {
  return captureSourcePhoto("拍照题目.jpg");
}

export async function editQuestionImage(file: LocalImportFile): Promise<LocalImportFile | null> {
  if (file.mimeType === "application/pdf") return null;
  try {
    const editedPath = await new Promise<string>((resolve, reject) => {
      if (typeof wx.editImage === "function") {
        wx.editImage({
          src: file.localPath,
          success: (result) => {
            resolve(result.tempFilePath);
          },
          fail: reject,
        });
        return;
      }
      if (typeof wx.cropImage === "function") {
        wx.cropImage({
          src: file.localPath,
          cropScale: "9:16",
          success: (result) => {
            resolve(result.tempFilePath);
          },
          fail: reject,
        });
        return;
      }
      reject(new Error("当前微信版本不支持图片编辑，可以直接使用原图。"));
    });
    const byteSize = await localFileSize(editedPath);
    return {
      ...file,
      localPath: editedPath,
      displayName: fileName(editedPath, "裁剪后的题目.jpg"),
      mimeType: editedPath.toLowerCase().split("?", 1)[0]?.endsWith(".png")
        ? "image/png"
        : "image/jpeg",
      byteSize,
    };
  } catch (error) {
    if (isUserCancellation(error)) return null;
    throw error;
  }
}

export async function chooseQuestionPdf(): Promise<LocalImportFile | null> {
  try {
    const result = await wx.chooseMessageFile({ count: 1, type: "all" });
    const file = result.tempFiles.at(0);
    if (!file) return null;
    const displayName = file.name || fileName(file.path, "导入题目.pdf");
    const normalizedName = displayName.toLowerCase().split("?", 1)[0] ?? "";
    const normalizedPath = file.path.toLowerCase().split("?", 1)[0] ?? "";
    if (!normalizedName.endsWith(".pdf") && !normalizedPath.endsWith(".pdf")) {
      throw new Error("请选择 PDF 文件。");
    }
    return {
      localPath: file.path,
      displayName,
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
    pageCount: null,
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

function fileName(path: string, fallback: string): string {
  const name = path.split("?", 1)[0]?.split("/").at(-1)?.trim();
  return name || fallback;
}

async function localFileSize(filePath: string): Promise<number> {
  const manager = wx.getFileSystemManager();
  return new Promise<number>((resolve, reject) => {
    manager.getFileInfo({
      filePath,
      success: (result) => {
        resolve(result.size);
      },
      fail: reject,
    });
  });
}

function isUserCancellation(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const rawMessage = (error as { errMsg?: unknown }).errMsg;
  const message = typeof rawMessage === "string" ? rawMessage.toLowerCase() : "";
  return message.includes("cancel");
}
