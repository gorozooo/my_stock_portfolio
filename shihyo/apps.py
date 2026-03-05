"""
[FILE] apps.py
[PATH] <project_root>/shihyo/apps.py

このファイルは何？
- Django がこのアプリ（shihyo: 指標専用）を認識するための設定ファイルです。
"""

from django.apps import AppConfig


class ShihyoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shihyo"
    verbose_name = "指標（先物・ドル円・VIX）"