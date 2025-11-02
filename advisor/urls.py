from django.urls import path
from advisor.views import page, api, watch_api, policy_page
from advisor.views import report as report_views
from advisor.views.line import webhook as advisor_line_webhook
from advisor.views.memo_page import memo_page, memos_list_api, memo_delete_api

urlpatterns = [
    path("board/", page.board_page, name="advisor_board_page"),     # 画面
    # --- Board / Action / Reminder / Ping ---
    path("api/board/", api.board_api, name="advisor_board"),
    path("api/action/", api.record_action, name="advisor_record_action"),
    path("api/remind/", api.create_reminder, name="advisor_create_reminder"),
    path("api/ping/", api.ping, name="advisor_ping"),
    
    path("watch/", page.watch_page, name="advisor_watch_page"),
    # --- Watch List APIs（既存のまま） ---
    path("api/watch/list/", watch_api.watch_list, name="advisor_watch_list"),
    path("api/watch/upsert/", watch_api.watch_upsert, name="advisor_watch_upsert"),
    path("api/watch/archive/", watch_api.watch_archive, name="advisor_watch_archive"),

    # デバッグ用
    path("api/debug/add/", api.debug_add, name="advisor_debug_add"),
    path("api/debug/add_reminder/", api.debug_add_reminder, name="advisor_debug_add_reminder"),

    path("api/watch/ping/", watch_api.watch_ping, name="advisor_watch_ping"),
    path("api/watch/archive/id/<int:rec_id>/", watch_api.watch_archive_by_id_get, name="advisor_watch_archive_by_id_get"),
    
    path("policy1/", policy_page.policy_page, name="advisor_policy_page"),
    path("api/policy/", policy_page.policy_api, name="advisor_policy_api"),
    
    path("advisor/report/<str:yyyymmdd>/", report_views.daily_report, name="advisor_daily_report"),
    
    path("line/webhook/", advisor_line_webhook, name="line_webhook"),
    
    # === 発注メモ ===
    path("memos/", memo_page, name="advisor_memo_page"),
    path("api/memos/list/", memos_list_api, name="advisor_memos_list_api"),
    path("api/memos/delete/<int:pk>/", memo_delete_api, name="advisor_memo_delete_api"),
    
]