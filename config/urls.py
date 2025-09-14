from django.http import HttpResponse
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok")), 
    path('admin/', admin.site.urls),
    path('', include('portfolio.urls')), 
]
