from django.shortcuts import render, redirect
from django.utils.timezone import now

def _get_mode(request):
    mode = request.session.get("aiapp_mode") or "LIVE"
    return mode

def dashboard(request):
    # ヘッダ表示用：更新日時はとりあえず現在時刻
    context = {
        "last_updated": now(),
        "mode": _get_mode(request),
        "regime": {"trend": 68, "meanrev": 32, "defense": 41},  # 仮値
        "themes": [("半導体", 38), ("インバウンド", 12), ("その他", 50)],
    }
    return render(request, "aiapp/dashboard.html", context)

def toggle_mode(request):
    cur = _get_mode(request)
    request.session["aiapp_mode"] = "DEMO" if cur == "LIVE" else "LIVE"
    # トグル＝強制リセット（ここでは単に画面再表示）
    return redirect("aiapp:dashboard")
