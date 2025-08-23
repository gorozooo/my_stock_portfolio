from django.contrib import admin
from .models import Stock, RealizedTrade, Cash, BottomTab, SubMenu, SettingsPassword

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('name', 'updated_at')

@admin.register(RealizedTrade)
class RealizedTradeAdmin(admin.ModelAdmin):
    list_display = ('name', 'updated_at')

@admin.register(Cash)
class CashAdmin(admin.ModelAdmin):
    list_display = ('amount', 'updated_at')

@admin.register(BottomTab)
class BottomTabAdmin(admin.ModelAdmin):
    list_display = ('name', 'icon', 'url_name', 'order')
    ordering = ('order',)

@admin.register(SubMenu)
class SubMenuAdmin(admin.ModelAdmin):
    list_display = ('name', 'tab', 'url', 'order')
    ordering = ('tab', 'order')

@admin.register(SettingsPassword)
class SettingsPasswordAdmin(admin.ModelAdmin):
    list_display = ('password',)
