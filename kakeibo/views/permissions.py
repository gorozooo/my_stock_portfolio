# =========================================
# [FILE] permissions.py
# [PATH] kakeibo/views/permissions.py
#
# このファイルは何？
# 家計簿アプリへのアクセス権を判定するロジック。
# 「kakeibo_users」グループ所属者のみ許可。
# =========================================

from django.conf import settings


def kakeibo_access_required(user):
    if not user.is_authenticated:
        return False

    return user.groups.filter(
        name=settings.KAKEIBO_GROUP_NAME
    ).exists()