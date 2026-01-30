"""
[FILE] autotrade/views.py
[PATH] <project_root>/autotrade/views.py

このファイルは何？
- ブラウザ（iPhone）から /autotrade/ を開いた時に、
  DBの今日レコードを取得し、テンプレに渡して表示する“画面の入口”です。

初心者ポイント：
- テンプレで難しい辞書参照をしないように、表示用のデータはここで整形して渡します。
"""

from datetime import date
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import AutoTradeDailyState


def _build_bt_rows_for_template(state: AutoTradeDailyState):
    """
    テンプレ側で「数字キー参照」などで詰まらないように、
    表示用に整形して返す。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    strategy = state.strategy or "BREAKOUT"
    s_bt = bt.get(strategy, {}) if isinstance(bt, dict) else {}

    rows = []
    for w in ["20", "60", "120"]:
        r = s_bt.get(w, {}) if isinstance(s_bt, dict) else {}
        rows.append({
            "window": w,
            "pf": r.get("pf"),
            "max_dd": r.get("max_dd"),
            "trades": r.get("trades"),
            "pnl": r.get("pnl"),
        })
    return rows


@login_required
def dashboard(request):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    ctx = {
        "state": state,
        "bt_rows": _build_bt_rows_for_template(state),
    }
    return render(request, "autotrade/dashboard.html", ctx)