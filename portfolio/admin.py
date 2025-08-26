from django.contrib import admin
from .models import Stock, RealizedTrade, Cash, BottomTab, SubMenu, SettingsPassword

# =============================
# Stock
# =============================
@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('id',)  # とりあえず id のみ

# =============================
# RealizedTrade
# =============================
@admin.register(RealizedTrade)
class RealizedTradeAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'updated_at')

# =============================
# Cash
# =============================
@admin.register(Cash)
class CashAdmin(admin.ModelAdmin):
    list_display = ('id', 'amount', 'updated_at')

# =============================
# BottomTab
# =============================
@admin.register(BottomTab)
class BottomTabAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'icon', 'url_name', 'order')
    ordering = ('order',)

# =============================
# SubMenu
# =============================
@admin.register(SubMenu)
class SubMenuAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'tab', 'url', 'order')
    ordering = ('tab', 'order')

# =============================
# SettingsPassword
# =============================
@admin.register(SettingsPassword)
class SettingsPasswordAdmin(admin.ModelAdmin):
    list_display = ('id', 'password')
