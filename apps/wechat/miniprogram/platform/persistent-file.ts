export interface PersistUserDataFileCommand {
  tempFilePath: string;
  filenamePrefix: string;
  extension: string;
  failureMessage: string;
}

export function persistUserDataFile(command: PersistUserDataFileCommand): Promise<string> {
  const fileSystem = wx.getFileSystemManager();
  const localPath = `${wx.env.USER_DATA_PATH}/${safeFilename(command.filenamePrefix)}-${uniqueToken()}.${safeExtension(command.extension)}`;
  return new Promise((resolve, reject) => {
    fileSystem.copyFile({
      srcPath: command.tempFilePath,
      destPath: localPath,
      success: () => {
        resolve(localPath);
      },
      fail: (error) => {
        reject(new Error(error.errMsg || command.failureMessage));
      },
    });
  });
}

function uniqueToken(): string {
  return `${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

function safeFilename(value: string): string {
  const name = value
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return name || "anan-file";
}

function safeExtension(value: string): string {
  const extension = value
    .trim()
    .replace(/^\.+/u, "")
    .replace(/[^A-Za-z0-9]+/g, "");
  return extension || "bin";
}
