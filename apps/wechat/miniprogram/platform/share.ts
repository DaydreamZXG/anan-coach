const shareTitle = "和岸岸一起，把备考计划落到今天";
const shareImageUrl = "/assets/icons/anan/logo-design-file.png";

/**
 * 分享只介绍产品，不携带当前页面、题目、成绩或用户标识。
 * 新用户从今日页进入时会按现有登录流程继续。
 */
export function appShareMessage() {
  return {
    title: shareTitle,
    path: "/pages/today/index?from=share",
    imageUrl: shareImageUrl,
  };
}

export function appTimelineShare() {
  return {
    title: shareTitle,
    query: "from=timeline",
    imageUrl: shareImageUrl,
  };
}

export function enableAppShareMenu(): void {
  void wx.showShareMenu({ menus: ["shareAppMessage", "shareTimeline"] });
}
