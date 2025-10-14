# portfolio/admin.py
from __future__ import annotations
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Holding, UserSetting, RealizedTrade, Dividend
from .models_cash import BrokerAccount, CashLedger, MarginState
from .models_advisor import AdviceSession, AdviceItem

# --------- Holding ---------
@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    """
    モデルの有無に合わせて list_display / search_fields / list_filter を動的に構成。
    （user を削除していても残していても動く）
    """

    def get_list_display(self, request):
        base = [
            *(["user"] if hasattr(Holding, "user") else []),
            "ticker",
            *(["name"] if hasattr(Holding, "name") else []),
            *(["quantity"] if hasattr(Holding, "quantity") else []),
            *(["avg_cost"] if hasattr(Holding, "avg_cost") else []),
            *(["broker"] if hasattr(Holding, "broker") else []),
            *(["side"] if hasattr(Holding, "side") else []),
            *(["account"] if hasattr(Holding, "account") else []),
            *(["created_at"] if hasattr(Holding, "created_at") else []),
            *(["updated_at"] if hasattr(Holding, "updated_at") else []),
        ]
        return tuple(base)

    def get_search_fields(self, request):
        fields = ["ticker"]
        if hasattr(Holding, "name"):
            fields.append("name")
        if hasattr(Holding, "user"):
            fields.append("user__username")
        return tuple(fields)

    def get_list_filter(self, request):
        flt = []
        if hasattr(Holding, "broker"):
            flt.append("broker")
        if hasattr(Holding, "side"):
            flt.append("side")
        if hasattr(Holding, "account"):
            flt.append("account")
        if hasattr(Holding, "updated_at"):
            flt.append("updated_at")
        return tuple(flt)


# --------- UserSetting ---------
try:
    admin.site.register(UserSetting)
except admin.sites.AlreadyRegistered:
    pass


# --------- RealizedTrade ---------
@admin.register(RealizedTrade)
class RealizedTradeAdmin(admin.ModelAdmin):
    """
    broker/account は日本語のラベルを表示。
    追加していれば hold_days も表示。
    """

    @admin.display(description="証券会社")
    def broker_jp(self, obj):
        try:
            return obj.get_broker_display()
        except Exception:
            return getattr(obj, "broker", "")

    @admin.display(description="口座区分")
    def account_jp(self, obj):
        try:
            return obj.get_account_display()
        except Exception:
            return getattr(obj, "account", "")

    def get_list_display(self, request):
        cols = [
            "trade_at",
            "ticker",
            "name",
            "broker_jp",
            "account_jp",
            "side",
            "qty",
            "price",
        ]
        if hasattr(RealizedTrade, "fee"):
            cols.append("fee")
        if hasattr(RealizedTrade, "tax"):
            cols.append("tax")
        if hasattr(RealizedTrade, "cashflow"):
            cols.append("cashflow")
        cols.append("pnl")  # property
        if hasattr(RealizedTrade, "hold_days"):
            cols.append("hold_days")
        return tuple(cols)

    def get_list_filter(self, request):
        flt = ["side", "trade_at"]
        if hasattr(RealizedTrade, "broker"):
            flt.insert(0, "broker")
        if hasattr(RealizedTrade, "account"):
            flt.insert(1, "account")
        return tuple(flt)

    def get_search_fields(self, request):
        fields = ["ticker", "name", "memo"]
        return tuple([f for f in fields if hasattr(RealizedTrade, f.split("__")[0])])


# --------- Dividend ---------
@admin.register(Dividend)
class DividendAdmin(admin.ModelAdmin):
    list_display = (
        "id", "date", "ticker", "name",
        "amount",        # 受取額（税引後）
        "tax",           # 税額
        "gross_display", # 税引前の概算（表示用）
        "holding",
    )
    list_filter = ("date", "is_net")
    search_fields = ("ticker", "name", "holding__ticker", "holding__name", "memo")
    ordering = ("-date", "-id")

    def gross_display(self, obj):
        """税引前（概算）表示用。is_net=True を前提に amount + tax を返す。"""
        amt = float(obj.amount or 0)
        tax = float(obj.tax or 0)
        return amt + tax if obj.is_net else amt
    gross_display.short_description = "税引前(概算)"


# --------- Cash ---------
@admin.register(BrokerAccount)
class BrokerAccountAdmin(admin.ModelAdmin):
    list_display = ("broker", "account_type", "currency", "opening_balance", "name")
    list_filter = ("broker", "account_type", "currency")
    search_fields = ("name",)


@admin.register(CashLedger)
class CashLedgerAdmin(admin.ModelAdmin):
    """
    CashLedger は source_type/source_id に一本化。
    旧 link_model/link_id は削除済み。
    """
    list_display = (
        "at",
        "account",
        "kind",
        "amount",
        "memo",
        "source_type",
        "source_id",
        "source_link",  # 関連元（Dividend/RealizedTrade）へのリンク
    )
    list_filter = (
        "kind",
        "source_type",
        ("account", admin.RelatedOnlyFieldListFilter),
        "account__broker",
        "account__account_type",
    )
    search_fields = ("memo", "account__broker")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "at")

    @admin.display(description="Source")
    def source_link(self, obj: CashLedger):
        """
        Dividend / RealizedTrade の変更ページへリンク。
        該当しなければ文字列を返す。
        """
        if not obj.source_type or not obj.source_id:
            return "—"

        try:
            if obj.source_type == CashLedger.SourceType.DIVIDEND:
                url = reverse("admin:portfolio_dividend_change", args=[obj.source_id])
                label = f"Dividend #{obj.source_id}"
            elif obj.source_type == CashLedger.SourceType.REALIZED:
                url = reverse("admin:portfolio_realizedtrade_change", args=[obj.source_id])
                label = f"RealizedTrade #{obj.source_id}"
            else:
                return f"{obj.source_type} #{obj.source_id}"
            return format_html('<a href="{}">{}</a>', url, mark_safe(label))
        except Exception:
            return f"{obj.source_type} #{obj.source_id}"


@admin.register(MarginState)
class MarginStateAdmin(admin.ModelAdmin):
    list_display = ("as_of", "account", "cash_free", "stock_collateral_value", "haircut_pct", "required_margin", "restricted_amount")
    list_filter = ("account__broker", "account__account_type")
    
# --------- AI ---------
@admin.register(AdviceSession)
class AdviceSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "note")
    list_filter  = ("created_at",)
    search_fields = ("note",)
    ordering = ("-created_at",)

@admin.register(AdviceItem)
class AdviceItemAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "kind", "score", "taken", "created_at")
    list_filter  = ("kind", "taken", "created_at")
    search_fields = ("message",)
    ordering = ("-created_at",)