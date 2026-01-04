# portfolio/urls.py
from django.urls import path
from django.http import HttpResponse
from django.urls import path, include

from .views import core as core_views
from .views import settings as settings_views
from .views import api as api_views
from .views import realized as realized_views
from .views.realized import (
    monthly_page,
    chart_monthly_json,
    chart_daily_heat_json,
)
from .views import dev_tools as dev_views
from .views import holding as hv
from .views import dividend as v_div  # ← 配当まわりはこのモジュールに集約
from .views import cash as v_cash
from .views import home
from .views import advisor as v_advisor
from portfolio.views.ab import set_variant, ab_dashboard
from .views.policy import policy_history
from .views import ops_advisor as v_ops
from .views import policy as policy_views
from .views.notify_dashboard import notify_dashboard
from portfolio.views.line import line_webhook
from portfolio.views import positions as positions_views
from portfolio.api import positions as api_positions
from portfolio.views import autopilot as autopilot_views

urlpatterns = [
    #path("", core_views.main, name="home"),
    path("", home.home, name="home"),
    
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

    # デバグ
    path("dev/scan-avg/", dev_views.scan_avg, name="scan_avg"),

    # 保有
    path("holdings/", hv.holding_list, name="holding_list"),
    path("holdings/<int:pk>/close", realized_views.close_sheet, name="holding_close_sheet"),
    path("holdings/<int:pk>/close/submit", realized_views.close_submit, name="holding_close_submit"),
    path("holdings/new/", hv.holding_create, name="holding_create"),
    path("holdings/<int:pk>/edit/", hv.holding_edit, name="holding_edit"),
    path("holdings/<int:pk>/delete/", hv.holding_delete, name="holding_delete"),
    path("api/ticker-name", hv.api_ticker_name, name="api_ticker_name"),
    path("holdings/partial/list", hv.holding_list_partial, name="holding_list_partial"),

    # 配当（ダッシュボード/一覧/作成・編集・削除/名前ルックアップ/CSV/目標保存/カレンダー/予測）
    path("dividends/dashboard/", v_div.dashboard, name="dividend_dashboard"),
    path("dividends/dashboard.json", v_div.dashboard_json, name="dividend_dashboard_json"),
    path("dividends/", v_div.dividend_list, name="dividend_list"),
    path("dividends/create/", v_div.dividend_create, name="dividend_create"),
    path("dividends/<int:pk>/edit/", v_div.dividend_edit, name="dividend_edit"),
    path("dividends/<int:pk>/delete/", v_div.dividend_delete, name="dividend_delete"),
    path("dividends/lookup-name/", v_div.dividend_lookup_name, name="dividend_lookup_name"),
    path("dividends/export.csv", v_div.export_csv, name="dividends_export_csv"),
    path("dividends/goal/", v_div.dividend_save_goal, name="dividend_save_goal"),
    path("dividends/calendar/", v_div.dividends_calendar, name="dividend_calendar"),
    path("dividends/calendar.json", v_div.dividends_calendar_json, name="dividend_calendar_json"),
    path("dividends/forecast/", v_div.dividends_forecast, name="dividend_forecast"),
    path("dividends/forecast.json", v_div.dividends_forecast_json, name="dividend_forecast_json"),

    # 実現損益（メイン）
    path("realized/", realized_views.list_page, name="realized_list"),
    path("realized/create", realized_views.create, name="realized_create"),
    path("realized/delete/<int:pk>", realized_views.delete, name="realized_delete"),
    path("realized/close-sheet/<int:pk>/", realized_views.close_sheet, name="realized_close_sheet"),
    path("realized/close-submit/<int:pk>/", realized_views.close_submit, name="realized_close_submit"),

    # サマリー/パーツ
    path("realized/summary-period", realized_views.summary_period_partial, name="realized_summary_period"),
    path("realized/summary-partial/", realized_views.summary_partial, name="realized_summary_partial"),
    path("realized/partial/table", realized_views.table_partial, name="realized_table_partial"),

    # ランキング
    path("realized/ranking/", realized_views.realized_ranking_partial, name="realized_ranking_partial"),
    path("realized/ranking_detail/", realized_views.realized_ranking_detail_partial, name="realized_ranking_detail_partial"),

    # 月別サマリー（専用ページ）
    path("realized/monthly/", monthly_page, name="realized_monthly"),
    path("realized/monthly/topworst/", realized_views.monthly_topworst_partial, name="realized_monthly_topworst"),
    path("realized/monthly/kpis/", realized_views.monthly_kpis_partial, name="realized_monthly_kpis"),
    path("realized/monthly/breakdown/", realized_views.monthly_breakdown_partial, name="realized_monthly_breakdown"),

    # チャートJSON
    path("realized/chart-monthly.json", chart_monthly_json, name="realized_chart_monthly"),
    path("realized/chart/monthly.json", chart_monthly_json, name="realized_chart_monthly_json"),
    path("realized/chart/daily/<int:year>/<int:month>.json",
         chart_daily_heat_json, name="realized_chart_daily_heat_json"),

    # CSV
    path("realized/export/csv", realized_views.export_csv, name="realized_export_csv"),
    
    # 現金
    path("cash/", v_cash.cash_dashboard, name="cash_dashboard"),
    path("cash/history/", v_cash.cash_history, name="cash_history"),                  # 一覧（初期）
    
    # AIアドバイザー API 
    path("api/advisor/latest/", v_advisor.latest_session_items, name="advisor-latest"),
    path("api/advisor/toggle/<int:item_id>/", v_advisor.toggle_taken, name="advisor-toggle"),
    path("api/advisor/has/", v_advisor.has_sessions, name="advisor-has"),
    path("advisor/set-variant/<str:v>/", set_variant, name="advisor_set_variant"),
    path("advisor/ab/", ab_dashboard, name="advisor_ab"),
    path("advisor/policy/", policy_history, name="advisor_policy"),
    path("api/advisor/learn/", v_ops.advisor_learn_now, name="advisor_learn_now"),
    path("advisor/policy/retrain/", policy_views.policy_retrain_apply, name="policy_retrain"),
    path("advisor/notify-dashboard/", notify_dashboard, name="notify_dashboard"),
    
    # LINE
    path("line/webhook/", line_webhook, name="line_webhook"),
    
    # positions
    path("positions/", positions_views.position_list, name="position_list"),
    path("api/positions/add", api_positions.add_position, name="api_positions_add"),    
    path("autopilot/", autopilot_views.autopilot_page, name="autopilot_page"),
    
    # advisorアプリ
    #path("advisor/", include("advisor.urls")),
    
]