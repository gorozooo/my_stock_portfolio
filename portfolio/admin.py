# portfolio/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import (
    Stock,
    RealizedProfit,
    Dividend,
    CashFlow,
    BottomTab,
    SubMenu,
    SettingsPassword,
)

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
# RealizedProfit（実現損益）
#   ※あなたのモデルに合わせて列名を設定しています。
#   - モデルのフィールド名が異なる場合は、下の list_display を調整してください。
# =============================
@admin.register(RealizedProfit)
class RealizedProfitAdmin(admin.ModelAdmin):
    list_display = (
        "date",          # 取引日（例：DateField / sold_at 等に合わせてOK）
        "stock_name",    # 銘柄名
        "code",          # 証券コード（モデルによっては ticker）
        "broker",        # 証券会社
        "account_type",  # 口座区分
        "trade_type",    # 売/買 or 種別
        "quantity",      # 株数
        "profit_amount", # 損益額
        "profit_rate",   # 損益率
    )
    list_filter = ("broker", "account_type", "trade_type", "date")
    search_fields = ("stock_name", "code")
    ordering = ("-date", "-id")


# =============================
# Dividend（配当）
# =============================
@admin.register(Dividend)
class DividendAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "received_at",
        "ticker",
        "stock_name",
        "gross_amount",
        "tax",
        "net_amount",
        "account_type",
        "broker",
        "updated_at",
    )
    search_fields = ("ticker", "stock_name", "broker")
    list_filter = ("received_at", "broker", "account_type")
    ordering = ("-received_at", "-id")
    readonly_fields = ("created_at", "updated_at", "net_amount")


# =============================
# CashFlow（入出金）
# =============================
@admin.register(CashFlow)
class CashFlowAdmin(admin.ModelAdmin):
    list_display = ("id", "occurred_at", "broker", "flow_type", "amount", "memo", "updated_at")
    list_filter  = ("broker", "flow_type", "occurred_at")
    search_fields = ("memo",)
    ordering = ("-occurred_at", "-id")


# =============================
# BottomTab
# =============================
@admin.register(BottomTab)
class BottomTabAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "icon", "url_name", "order")
    ordering = ("order",)


# =============================
# SubMenu
# =============================
@admin.register(SubMenu)
class SubMenuAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "tab", "url", "order")
    ordering = ("tab", "order")


# =============================
# SettingsPassword
# =============================
@admin.register(SettingsPassword)
class SettingsPasswordAdmin(admin.ModelAdmin):
    list_display = ("id", "password")