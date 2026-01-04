from django.shortcuts import render, redirect
from django.utils.timezone import now

def _get_mode(request):
    return request.session.get("aiapp_mode") or "LIVE"

def dashboard(request):
    context = {
        "last_updated": now(),
        "mode": _get_mode(request),
        "regime": {
            "trend": 68,
            "meanrev": 32,
            "defense": 41,
        },
    }
    return render(request, "aiapp/dashboard.html", context)

def toggle_mode(request):
    cur = _get_mode(request)
    request.session["aiapp_mode"] = "DEMO" if cur == "LIVE" else "LIVE"
    return redirect("aiapp:dashboard")