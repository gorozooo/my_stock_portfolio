from django.urls import path
from advisor.views import page, api

urlpatterns = [
    path("board/", page.board_page, name="advisor_board_page"),     # 画面
    path("api/board/", api.board_api, name="advisor_board_api"),    # JSON
    path("api/action/", api.record_action, name="advisor_record_action"),      # ← 追加
    path("api/remind/", api.create_reminder, name="advisor_create_reminder"),  # ← 追加

]