# portfolio/admin.py
from django.contrib import admin
from .models import Holding, UserSetting, RealizedTrade


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
    追加していれば holding_days も表示。
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
        # もし保有日数フィールドを追加済みなら表示
        if hasattr(RealizedTrade, "holding_days"):
            cols.append("holding_days")
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