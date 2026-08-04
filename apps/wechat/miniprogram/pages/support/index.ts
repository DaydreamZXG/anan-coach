Page({
  data: {
    supportQrImageUrl: "/assets/wechat-support-qr.jpg",
  },

  previewGroupQr() {
    const url = this.data.supportQrImageUrl;
    void wx.previewImage({ current: url, urls: [url] });
  },
});
