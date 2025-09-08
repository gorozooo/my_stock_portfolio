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
    list_display = (
        'date','stock_name','code','broker','account_type',
        'trade_type','quantity','profit_amount','profit_rate'
    )
    list_filter  = ('broker','account_type','trade_type','date')
    search_fields= ('stock_name','code')

# =============================
# 配当入力
# =============================
from .models import Dividend

@admin.register(Dividend)
class DividendAdmin(admin.ModelAdmin):
    list_display = ("id","received_at","ticker","stock_name","gross_amount","tax","net_amount","account_type","broker","updated_at")
    search_fields = ("ticker","stock_name","broker")
    list_filter = ("received_at","broker","account_type")
    ordering = ("-received_at","-id")
    readonly_fields = ("created_at","updated_at","net_amount")

# =============================
# Cash
# =============================
@admin.register(Cash)
class CashAdmin(admin.ModelAdmin):
    list_display = ('id', 'amount', 'updated_at')

# =============================
# 入出金
# =============================
from .models import CashFlow

@admin.register(CashFlow)
class CashFlowAdmin(admin.ModelAdmin):
    list_display = ("id","occurred_at","broker","flow_type","amount","memo","updated_at")
    list_filter  = ("broker","flow_type","occurred_at")
    search_fields = ("memo",)
    ordering = ("-occurred_at","-id")

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
