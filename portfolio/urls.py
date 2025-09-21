from django.urls import path
from django.http import HttpResponse

from .views import core as core_views
from .views import settings as settings_views
from .views import api as api_views
from .views import realized as realized_views

urlpatterns = [
    path("", core_views.main, name="home"),

    # トレンド
    path("trend/", core_views.trend_page, name="trend"),
    path("api/trend", core_views.trend_api, name="trend_api"),
    path("trend/card", core_views.trend_card_partial, name="trend_card_partial"),

    # ヘルスチェック
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),

    # 新API
    path("api/metrics", api_views.metrics, name="api_metrics"),
    path("api/ohlc", api_views.ohlc, name="api_ohlc"),

    # 設定
    path("settings/trade", settings_views.trade_setting, name="trade_setting"),

    # 保有
    path("holdings/", core_views.holdings_list, name="holdings_list"),
    path("holdings/<int:pk>/close", realized_views.close_sheet, name="holding_close_sheet"),  # 画面
    path("holdings/<int:pk>/close/submit", realized_views.close_submit, name="holding_close_submit"),  # POST

    # 実現損益
    path("realized/", realized_views.list_page, name="realized_list"),
    path("realized/create", realized_views.create, name="realized_create"),
    path("realized/delete/<int:pk>", realized_views.delete, name="realized_delete"),
    path("realized/close-sheet/<int:pk>/", realized_views.close_sheet, name="realized_close_sheet"),
    path("realized/close-submit/<int:pk>/", realized_views.close_submit, name="realized_close_submit"),
    path("realized/summary-period", realized_views.summary_period_partial, name="realized_summary_period"),
    path("realized/chart-monthly.json", realized_views.chart_monthly_json, name="realized_chart_monthly"),
    path("realized/summary-partial/", realized_views.realized_summary_partial, name="realized_summary_partial"),
    path("realized/ranking/", realized_views.realized_ranking_partial, name="realized_ranking_partial"),
    path("realized/ranking_detail/", realized_views.realized_ranking_detail_partial, name="realized_ranking_detail_partial"),
    
    # ← 追加：部分テンプレとCSV
    path("realized/partial/table", realized_views.table_partial, name="realized_table_partial"),
    path("realized/partial/summary", realized_views.summary_partial, name="realized_summary_partial"),
    path("realized/export/csv", realized_views.export_csv, name="realized_export_csv"),
]