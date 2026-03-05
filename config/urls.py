from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.http import HttpResponse


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")

urlpatterns = [
    path("admin/", admin.site.urls),

    # 株
    path("", include("portfolio.urls")),

    # AI
    path("aiapp/", include("aiapp.urls")),

    # 自動売買
    path("autotrade/", include("autotrade.urls")),

    # ★ 家計簿（新規）
    path("kakeibo/", include("kakeibo.urls")),
    
    # ★ 指標（新規）
    path("shihyo/", include("shihyo.urls")),
    
    # ★ health check 専用
    path("healthz/", healthz),
    
    # PWA: manifest / service worker
    path(
        "manifest.webmanifest",
        TemplateView.as_view(
            template_name="pwa/manifest.webmanifest",
            content_type="application/manifest+json",
        ),
        name="manifest"
    ),
    path(
        "service-worker.js",
        TemplateView.as_view(
            template_name="pwa/service-worker.js",
            content_type="application/javascript",
        ),
        name="service_worker"
    ),

    # Django標準ログイン
    path("accounts/", include("django.contrib.auth.urls")),
]