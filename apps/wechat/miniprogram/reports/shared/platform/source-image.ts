export interface LocalSourceImageFile {
  localPath: string;
  displayName: string;
  sourceKind: "camera_image" | "image_import";
  mimeType: "image/jpeg" | "image/png";
  byteSize: number;
  pageCount: null;
}

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
    content: "相机仅用于拍摄你选择导入的学习图片，未确认的识别结果不会进入学习诊断。",
    confirmText: "去设置",
    cancelText: "暂不开启",
  });
  if (!result.confirm) return false;
  const setting = await wx.openSetting();
  return setting.authSetting["scope.camera"] === true;
}

export async function chooseSourceImage(
  source: "camera" | "album",
  fallbackName: string,
): Promise<LocalSourceImageFile | null> {
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
      displayName: localFileName(path, fallbackName),
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

export async function captureSourcePhoto(fallbackName: string): Promise<LocalSourceImageFile> {
  const result = await new Promise<WechatMiniprogram.TakePhotoSuccessCallbackResult>(
    (resolve, reject) => {
      wx.createCameraContext().takePhoto({
        quality: "high",
        success: resolve,
        fail: reject,
      });
    },
  );
  const path = result.tempImagePath;
  return {
    localPath: path,
    displayName: localFileName(path, fallbackName),
    sourceKind: "camera_image",
    mimeType: imageMimeType(path),
    byteSize: await localFileSize(path),
    pageCount: null,
  };
}

function imageMimeType(path: string): "image/jpeg" | "image/png" {
  return path.toLowerCase().split("?", 1)[0]?.endsWith(".png") ? "image/png" : "image/jpeg";
}

function localFileName(path: string, fallback: string): string {
  const name = path.split("?", 1)[0]?.split("/").at(-1)?.trim();
  return name || fallback;
}

async function localFileSize(filePath: string): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    wx.getFileSystemManager().getFileInfo({
      filePath,
      success: ({ size }) => {
        resolve(size);
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
