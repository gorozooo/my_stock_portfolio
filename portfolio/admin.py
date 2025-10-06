# portfolio/admin.py
from django.contrib import admin
from .models import Holding, UserSetting, RealizedTrade, Dividend
from .models_cash import BrokerAccount, CashLedger, MarginState

# --------- Holding ---------
@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    """
    モデルの有無に合わせて list_display / search_fields / list_filter を動的に構成。
    （user を削除していても残していても動く）
    """

    def get_list_display(self, request):
        base = [
            # user があれば先頭に出す
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
# すでに UserSetting が登録済みでも二重登録を避ける
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

    # 日本語ラベル表示
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
        # プロパティ pnl をそのまま表示（数値）
        cols.append("pnl")
        # ★ 修正: 正しいフィールド名は hold_days
        if hasattr(RealizedTrade, "hold_days"):
            cols.append("hold_days")
        return tuple(cols)

    def get_list_filter(self, request):
        flt = ["side", "trade_at"]
        if hasattr(RealizedTrade, "broker"):
            flt.insert(0, "broker")  # 先頭寄りに
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
        "tax",           # 税額（自動計算）
        "gross_display", # 税引前の概算（表示用メソッド）
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

# --------- Dividend --------- 
@admin.register(BrokerAccount)
class BrokerAccountAdmin(admin.ModelAdmin):
    list_display = ("broker", "account_type", "currency", "opening_balance", "name")
    list_filter = ("broker", "account_type", "currency")
    search_fields = ("name",)

@admin.register(CashLedger)
class CashLedgerAdmin(admin.ModelAdmin):
    list_display = ("at", "account", "kind", "amount", "memo", "link_model", "link_id")
    list_filter = ("kind", "account__broker", "account__account_type")
    search_fields = ("memo", "link_model")

@admin.register(MarginState)
class MarginStateAdmin(admin.ModelAdmin):
    list_display = ("as_of", "account", "cash_free", "stock_collateral_value", "haircut_pct", "required_margin", "restricted_amount")
    list_filter = ("account__broker", "account__account_type")
