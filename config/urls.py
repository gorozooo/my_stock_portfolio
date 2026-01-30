from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("portfolio.urls")),
    path("aiapp/", include("aiapp.urls")),
    path("autotrade/", include("autotrade.urls")),

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