from django.contrib import admin
from .models import Stock, RealizedProfit, Cash, BottomTab, SubMenu, SettingsPassword
from django.utils.html import format_html
from django.urls import reverse

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
        "edit_link",   # 通常画面の編集ページ
        "sell_link",   # 通常画面の売却ページ
    )

    # 検索対象フィールド
    search_fields = ("ticker", "name", "sector")

    # 絞り込みフィルター
    list_filter = ("account_type", "sector", "purchase_date")

    # ソート順
    ordering = ("-purchase_date",)

    # 管理画面の入力フォームで編集不可にするフィールド
    readonly_fields = ("created_at", "updated_at")

    # ===== カスタム列（通常ビューへのリンク） =====
    def edit_link(self, obj):
        """ユーザー用の編集画面へ飛ぶボタン"""
        try:
            url = reverse("stock_edit", kwargs={"pk": obj.id})
        except Exception:
            url = "#"
        return format_html('<a class="button" href="{}" target="_blank">編集</a>', url)
    edit_link.short_description = "編集（通常画面）"

    def sell_link(self, obj):
        """ユーザー用の売却画面へ飛ぶボタン"""
        try:
            url = reverse("stock_sell", kwargs={"pk": obj.id})
        except Exception:
            url = "#"
        return format_html('<a class="button" href="{}" target="_blank">売却</a>', url)
    sell_link.short_description = "売却（通常画面）"
    
# =============================
# RealizedProfit
# =============================
@admin.register(RealizedProfit)
class RealizedProfitAdmin(admin.ModelAdmin):
    # 一覧に表示するカラム
    list_display = (
        "id",
        "sold_at",        # 売却日
        "ticker",
        "stock_name",
        "shares",
        "purchase_price",
        "sell_price",
        "total_profit",
    )

    # 検索対象フィールド
    search_fields = ("ticker", "stock_name")

    # 絞り込みフィルター
    list_filter = ("sold_at",)

    # ソート順
    ordering = ("-sold_at", "-id")
        
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
