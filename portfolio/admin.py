# [FILE] admin.py
# [PATH] portfolio/admin.py
#
# このファイルは何？
# - portfolio アプリの Django 管理画面設定です。
# - Holding / RealizedTrade / Dividend / CashLedger / TradeEvent などを
#   管理画面から見やすく・編集しやすくするための定義です。
#
# 今回の修正ポイント
# - TradeEvent を管理画面から編集 / 削除できるようにする
# - TradeEvent を削除した時、対応する CashLedger も同時に削除する
# - source_type の表記ゆれがあっても孤立 CashLedger を残しにくくする
# - CashLedger の Source リンクから TradeEvent にも飛べるようにする

from __future__ import annotations

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Holding, UserSetting, RealizedTrade, Dividend, TradeEvent
from .models_cash import BrokerAccount, CashLedger, MarginState
from .models_advisor import AdviceSession, AdviceItem, AdvicePolicy, AdvisorMetrics


def _tradeevent_source_types() -> list[str]:
    """
    CashLedger.source_type の過去データや表記ゆれも吸収するための候補一覧。
    """
    values = {
        "TradeEvent",
        "TRADEEVENT",
        "TRADE_EVENT",
        "TRADE",
        "TRD",
    }

    try:
        source_enum = getattr(CashLedger, "SourceType", None)
        if source_enum is not None:
            for attr in ("TRADE_EVENT", "TRADE", "TRD"):
                if hasattr(source_enum, attr):
                    raw = getattr(source_enum, attr)
                    if raw:
                        values.add(str(raw))
    except Exception:
        pass

    return list(values)


def _delete_cashledgers_for_tradeevent_ids(event_ids: list[int]) -> None:
    """
    TradeEvent に紐づく CashLedger を source_type/source_id ベースで削除する。
    """
    ids = [int(x) for x in event_ids if x is not None]
    if not ids:
        return

    CashLedger.objects.filter(
        source_id__in=ids,
        source_type__in=_tradeevent_source_types(),
    ).delete()


# --------- Holding ---------
@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    """
    モデルの有無に合わせて list_display / search_fields / list_filter を動的に構成。
    （user / sector を保持していてもいなくても動く）
    """

    def get_list_display(self, request):
        base = [
            *(["user"] if hasattr(Holding, "user") else []),
            "ticker",
            *(["name"] if hasattr(Holding, "name") else []),
            *(["sector"] if hasattr(Holding, "sector") else []),
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
        if hasattr(Holding, "sector"):
            fields.append("sector")
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
        if hasattr(Holding, "sector"):
            flt.append("sector")
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
        cols.append("pnl")
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


# --------- TradeEvent ---------
@admin.register(TradeEvent)
class TradeEventAdmin(admin.ModelAdmin):
    """
    TradeEvent を管理画面から直接編集 / 削除できるようにする。
    削除時は対応する CashLedger も同時に掃除する。
    """

    list_select_related = ("holding", "realized_trade", "user")

    @admin.display(description="Holding")
    def holding_link(self, obj):
        if not getattr(obj, "holding_id", None):
            return "—"
        try:
            url = reverse("admin:portfolio_holding_change", args=[obj.holding_id])
            return format_html('<a href="{}">Holding #{}</a>', url, obj.holding_id)
        except Exception:
            return f"Holding #{obj.holding_id}"

    @admin.display(description="Realized")
    def realized_trade_link(self, obj):
        if not getattr(obj, "realized_trade_id", None):
            return "—"
        try:
            url = reverse("admin:portfolio_realizedtrade_change", args=[obj.realized_trade_id])
            return format_html('<a href="{}">RealizedTrade #{}</a>', url, obj.realized_trade_id)
        except Exception:
            return f"RealizedTrade #{obj.realized_trade_id}"

    def get_list_display(self, request):
        cols = [
            "id",
            *(["user"] if hasattr(TradeEvent, "user") else []),
            *(["trade_at"] if hasattr(TradeEvent, "trade_at") else []),
            *(["event_type"] if hasattr(TradeEvent, "event_type") else []),
            *(["ticker"] if hasattr(TradeEvent, "ticker") else []),
            *(["name"] if hasattr(TradeEvent, "name") else []),
            *(["broker"] if hasattr(TradeEvent, "broker") else []),
            *(["account"] if hasattr(TradeEvent, "account") else []),
            *(["side"] if hasattr(TradeEvent, "side") else []),
            *(["qty"] if hasattr(TradeEvent, "qty") else []),
            *(["price"] if hasattr(TradeEvent, "price") else []),
            *(["cash_amount_jpy"] if hasattr(TradeEvent, "cash_amount_jpy") else []),
            "holding_link",
            "realized_trade_link",
        ]
        return tuple(cols)

    def get_search_fields(self, request):
        fields = []
        for f in ("ticker", "name", "memo", "position_key"):
            if hasattr(TradeEvent, f):
                fields.append(f)
        if hasattr(TradeEvent, "user"):
            fields.append("user__username")
        return tuple(fields)

    def get_list_filter(self, request):
        flt = []
        for f in ("event_type", "broker", "account", "side", "trade_at"):
            if hasattr(TradeEvent, f):
                flt.append(f)
        return tuple(flt)

    def get_readonly_fields(self, request, obj=None):
        ro = []
        for f in ("created_at", "updated_at"):
            if hasattr(TradeEvent, f):
                ro.append(f)
        return tuple(ro)

    def get_ordering(self, request):
        ordering = []
        if hasattr(TradeEvent, "trade_at"):
            ordering.append("-trade_at")
        ordering.append("-id")
        return tuple(ordering)

    def delete_model(self, request, obj):
        _delete_cashledgers_for_tradeevent_ids([obj.id])
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        ids = list(queryset.values_list("id", flat=True))
        _delete_cashledgers_for_tradeevent_ids(ids)
        super().delete_queryset(request, queryset)


# --------- Dividend ---------
@admin.register(Dividend)
class DividendAdmin(admin.ModelAdmin):
    list_display = (
        "id", "date", "ticker", "name",
        "amount",
        "tax",
        "gross_display",
        "holding",
    )
    list_filter = ("date", "is_net")
    search_fields = ("ticker", "name", "holding__ticker", "holding__name", "memo")
    ordering = ("-date", "-id")

    def gross_display(self, obj):
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
        "id",
        "at",
        "account",
        "kind",
        "amount",
        "memo",
        "source_type",
        "source_id",
        "source_link",
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
        if not obj.source_type or not obj.source_id:
            return "—"

        source_type = str(obj.source_type or "").strip()
        source_type_upper = source_type.upper()

        try:
            if obj.source_type == CashLedger.SourceType.DIVIDEND:
                url = reverse("admin:portfolio_dividend_change", args=[obj.source_id])
                label = f"Dividend #{obj.source_id}"
                return format_html('<a href="{}">{}</a>', url, mark_safe(label))

            if obj.source_type == CashLedger.SourceType.REALIZED:
                url = reverse("admin:portfolio_realizedtrade_change", args=[obj.source_id])
                label = f"RealizedTrade #{obj.source_id}"
                return format_html('<a href="{}">{}</a>', url, mark_safe(label))

            if source_type_upper in {"TRADEEVENT", "TRADE_EVENT", "TRADE", "TRD"}:
                url = reverse("admin:portfolio_tradeevent_change", args=[obj.source_id])
                label = f"TradeEvent #{obj.source_id}"
                return format_html('<a href="{}">{}</a>', url, mark_safe(label))

            return f"{obj.source_type} #{obj.source_id}"
        except Exception:
            return f"{obj.source_type} #{obj.source_id}"


@admin.register(MarginState)
class MarginStateAdmin(admin.ModelAdmin):
    list_display = (
        "as_of",
        "account",
        "cash_free",
        "stock_collateral_value",
        "haircut_pct",
        "required_margin",
        "restricted_amount",
    )
    list_filter = ("account__broker", "account__account_type")


# --------- AI ---------
@admin.register(AdviceSession)
class AdviceSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "note")
    date_hierarchy = "created_at"


@admin.register(AdviceItem)
class AdviceItemAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "kind", "score", "taken", "created_at")
    list_filter = ("kind", "taken")
    search_fields = ("message",)
    date_hierarchy = "created_at"


@admin.register(AdvicePolicy)
class AdvicePolicyAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "enabled", "updated_at", "created_at")
    list_filter = ("kind", "enabled")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AdvisorMetrics)
class AdvisorMetricsAdmin(admin.ModelAdmin):
    list_display = ("created_at", "engine", "policy", "train_acc", "n")
    list_filter = ("engine",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)