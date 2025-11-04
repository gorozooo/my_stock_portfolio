# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("portfolio.urls")),
    path('ai/', include('ai.urls')),

    # PWA: manifest / service worker をルート直下にぶら下げる
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

    # Django標準ログインUI（必要なら）
    path("accounts/", include("django.contrib.auth.urls")),
]