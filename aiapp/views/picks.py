from django.shortcuts import render
from django.utils.timezone import now

def _dummy_picks():
    # まずはダミー10銘柄。次フェーズで実データ化（services/scoring等）
    base = [
        {
            "name": "ソニーG", "code": "6758", "sector": "電機",
            "score": 82, "stars": 4, "rcp": 74,
            "entry": 12200, "tp": 12800, "sl": 11900,
            "qty": 100, "funds": 1220000, "pl_gain": 60000, "pl_loss": 30000,
            "reasons": [
                "週足×日足が上向き", "TOPIX比 上位20%", "出来高20MA比 1.8倍",
                "レジスタンス終値突破", "ATRは許容内"
            ],
            "concern": "決算直後で振れやすい",
        }
    ]
    # ダミーを10個に拡張（コードだけ変える）
    out = []
    for i in range(10):
        d = base[0].copy()
        d["code"] = f"67{58+i}"
        d["name"] = f"ソニーG{i+1}"
        d["score"] = 82 - (i % 5)
        d["stars"] = 3 + (i % 3)
        d["rcp"] = 70 + (i % 10)
        out.append(d)
    return out

def picks(request):
    context = {
        "last_updated": now(),
        "mode": request.session.get("aiapp_mode") or "LIVE",
        "items": _dummy_picks(),
    }
    return render(request, "aiapp/picks.html", context)
