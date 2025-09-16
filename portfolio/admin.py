# portfolio/admin.py
from django.contrib import admin
from .models import Holding, UserSetting

@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ("user", "ticker", "quantity", "avg_cost", "updated_at")
    search_fields = ("ticker", "name", "user__username")

# すでにUserSetting登録済みならOK
try:
    admin.site.register(UserSetting)
except Exception:
    pass