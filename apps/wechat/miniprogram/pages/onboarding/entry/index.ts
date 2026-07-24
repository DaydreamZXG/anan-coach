import { routes } from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";

Page({
  onLoad() {
    if (!currentAuthSession()) {
      void wx.reLaunch({ url: routes.login });
    }
  },

  back() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.reLaunch({ url: routes.login });
  },

  startProfile() {
    void wx.redirectTo({ url: routes.onboardingPreparationExperience });
  },

  openArchive() {
    void wx.redirectTo({ url: routes.profileArchive });
  },
});
