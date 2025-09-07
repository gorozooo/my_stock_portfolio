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
        "edit_link",   # ← 追加：通常画面の編集へ
        "sell_link",   # ← 追加：通常画面の売却へ
    )

    # 検索対象フィールド
    search_fields = ("ticker", "name", "sector")

    # 絞り込みフィルター
    list_filter = ("account_type", "sector", "purchase_date")

    # ソート順
    ordering = ("-purchase_date",)

    # 管理画面の入力フォームで編集不可にするフィールド
    readonly_fields = ("created_at", "updated_at")

    # ===== カスタム列（通常ビューへの導線） =====
    def edit_link(self, obj):
        """
        通常画面の編集ビューへ（URL名: stock_edit / 位置引数: stock.id）
        例：urls.py 側が path("stocks/<int:stock_id>/edit/", ..., name="stock_edit")
        かつテンプレ側が {% url 'stock_edit' stock.id %} の想定
        """
        try:
            url = reverse("stock_edit", args=[obj.id])
        except Exception:
            url = "#"
        return format_html('<a class="button" href="{}" target="_blank">編集</a>', url)

    edit_link.short_description = "編集（通常画面）"

    def sell_link(self, obj):
        """
        通常画面の売却ビューへ（URL名: stock_sell / 位置引数: stock.id）
        """
        try:
            url = reverse("stock_sell", args=[obj.id])
        except Exception:
            url = "#"
        return format_html('<a class="button" href="{}" target="_blank">売却</a>', url)

    sell_link.short_description = "売却（通常画面）"

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
