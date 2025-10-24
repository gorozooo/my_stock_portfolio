from django.urls import path
from advisor.views import page, api

urlpatterns = [
    path("board/", page.board_page, name="advisor_board_page"),     # 画面
    path("api/board/", api.board_api, name="advisor_board_api"),    # JSON
]