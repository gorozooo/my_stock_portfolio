from django.contrib import admin
from .models import Stock, RealizedTrade, Cash, BottomTab, SubMenu, SettingsPassword

# =============================
# Stock
# =============================
@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    # 一覧に表示するカラム
    list_display = (
        "id",
        "purchase_date",
        "ticker",
        "name",
        "account_type",
        "sector",
        "shares",
        "unit_price",
        "total_cost",
        "created_at",
        "updated_at",
    )

    # 検索対象フィールド
    search_fields = ("ticker", "name", "sector")

    # 絞り込みフィルター
    list_filter = ("account_type", "sector", "purchase_date")

    # ソート順
    ordering = ("-purchase_date",)

    # 管理画面の入力フォームで編集不可にするフィールド
    readonly_fields = ("created_at", "updated_at")

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
