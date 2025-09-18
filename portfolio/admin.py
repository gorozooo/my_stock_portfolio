# portfolio/admin.py
from django.contrib import admin
from .models import Holding, UserSetting, RealizedTrade

@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ("user", "ticker", "quantity", "avg_cost", "updated_at")
    search_fields = ("ticker", "name", "user__username")

# すでにUserSetting登録済みならOK
try:
    admin.site.register(UserSetting)
except Exception:
    pass
    

@admin.register(RealizedTrade)
class RealizedTradeAdmin(admin.ModelAdmin):
    list_display = ("trade_at", "ticker", "name", "broker", "side", "qty", "price", "fee", "tax", "cashflow", "pnl")
    list_filter  = ("broker", "side", "trade_at")
    search_fields= ("ticker", "name", "memo")
    