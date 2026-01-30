"""
[FILE] autotrade/apps.py
[PATH] <project_root>/autotrade/apps.py

このファイルは何？
- Djangoに「autotradeアプリが存在する」ことを知らせる設定ファイルです。

初心者ポイント：
- 通常は触りません。
"""

from django.apps import AppConfig


class AutotradeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "autotrade"
    verbose_name = "Auto DayTrade"