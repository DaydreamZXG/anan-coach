import { environment } from "../../config/environment";

Page({
  data: {
    groupQrImageUrl: environment.supportGroupQrImageUrl ?? "",
    qrAvailable: Boolean(environment.supportGroupQrImageUrl),
  },

  previewGroupQr() {
    const url = this.data.groupQrImageUrl;
    if (!url) return;
    void wx.previewImage({ current: url, urls: [url] });
  },
});
