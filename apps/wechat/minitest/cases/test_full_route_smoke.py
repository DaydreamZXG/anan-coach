"""60 条注册业务路由的真实后端云测渲染冒烟。"""

import minium

from minium_support import REGISTERED_ROUTES, open_rendered_page, route_test_name


class BFullRouteRenderSmokeTest(minium.MiniTest):
    """每条路由独立成案，失败报告能直接指出具体页面。"""


def _make_route_test(route):
    def route_test(self):
        result = open_rendered_page(self, route)
        self.assertIn(result["outcome"], ("state", "rendered", "redirect"))

    route_test.__name__ = route_test_name(route)
    route_test.__doc__ = "打开并等待页面稳定：%s" % route
    return route_test


for _route in REGISTERED_ROUTES:
    setattr(BFullRouteRenderSmokeTest, route_test_name(_route), _make_route_test(_route))
