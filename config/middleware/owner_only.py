# =========================================
# [FILE] owner_only.py
# [PATH] <project_root>/config/middleware/owner_only.py
#
# このファイルは何？
# 「あなた専用にしたいURL」をまとめてブロックするミドルウェア。
# - portfolio（/） / aiapp（/aiapp/） / autotrade（/autotrade/） / admin（/admin/）をあなた専用にする
# - kakeibo（/kakeibo/）は夫婦共有なのでブロックしない（kakeibo側のグループ制御に任せる）
# - accounts/login などは通す（ログインできないと困る）
# =========================================

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from urllib.parse import urlencode


class OwnerOnlyMiddleware:
    """
    あなた専用エリアへ、あなた以外がアクセスしたら 403 にする。
    未ログインならログイン画面へリダイレクト。
    """

    def __init__(self, get_response):
        self.get_response = get_response

        # ✅ 誰でも通してよいURL（静的/ログイン/PWAなど）
        self.PUBLIC_PREFIXES = (
            "/accounts/",
            "/kakeibo/",          # 家計簿は夫婦共有（詳細制御はkakeibo側）
            "/static/",
            "/media/",
        )

        # ✅ prefix一致以外で「完全一致で通す」もの（PWAなど）
        self.PUBLIC_EXACT = (
            "/manifest.webmanifest",
            "/service-worker.js",
            "/favicon.ico",
            "/healthz/",
        )

        # ✅ あなた専用にするURL
        # portfolioは「/」配下なので最後に処理する（他のpublicやkakeiboより後）
        self.OWNER_ONLY_PREFIXES = (
            "/aiapp/",
            "/autotrade/",
            "/admin/",
        )

    def __call__(self, request):
        path = request.path

        # 1) 公開（または共用）として通すもの
        if path in self.PUBLIC_EXACT or path.startswith(self.PUBLIC_PREFIXES):
            return self.get_response(request)

        # 2) aiapp / autotrade / admin はあなた専用
        if path.startswith(self.OWNER_ONLY_PREFIXES):
            return self._enforce_owner(request)

        # 3) 残りは全部「portfolio領域（/ 配下）」としてあなた専用にする
        #    例: "/" "/holdings/" など
        return self._enforce_owner(request)

    def _enforce_owner(self, request):
        # 未ログインならログイン画面へ（next付き）
        if not request.user.is_authenticated:
            next_url = request.get_full_path()
            login_url = settings.LOGIN_URL

            # nextの安全チェック（念のため）
            if not url_has_allowed_host_and_scheme(
                url=next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                next_url = "/"

            return redirect(f"{login_url}?{urlencode({'next': next_url})}")

        # ログイン済みでも、ユーザー名があなた以外なら拒否
        owner = getattr(settings, "STOCKS_OWNER_USERNAME", None) or "gorozooo"
        if request.user.get_username() != owner:
            raise PermissionDenied("Owner only.")

        return self.get_response(request)