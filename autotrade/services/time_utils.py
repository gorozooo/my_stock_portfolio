"""
[FILE] autotrade/services/time_utils.py
[PATH] <project_root>/autotrade/services/time_utils.py

このファイルは何？
- 日本時間（Asia/Tokyo）で “今日の時刻” を扱う小物ユーティリティです。

初心者ポイント：
- 時刻処理が散らばるとバグりやすいので、ここに集めます。
"""

from datetime import datetime, time
from django.utils import timezone


def now_jst():
    return timezone.localtime(timezone.now())


def parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def today_time(hhmm: str):
    t = parse_hhmm(hhmm)
    n = now_jst()
    return datetime(n.year, n.month, n.day, t.hour, t.minute, tzinfo=n.tzinfo)