"""
[FILE] autotrade/services/universe_service.py
[PATH] <project_root>/autotrade/services/universe_service.py

このファイルは何？
- 「今日の対象銘柄（5〜10）」を作るサービスです。

今の段階（第1弾）：
- まだ出来高/売買代金での自動選別は入れず、
  “候補の先頭から10件”でまず土台を動かします。

次フェーズ：
- ここに出来高/売買代金/ボラでのランキングを追加して強化します。
"""

from .universe_candidates_repo import load_candidates


def build_daily_universe(limit: int = 10):
    candidates = load_candidates()
    picks = candidates[:max(5, min(limit, 10))]

    return {
        "picks": [{"ticker": t, "reason": "流動性候補（暫定）"} for t in picks]
    }