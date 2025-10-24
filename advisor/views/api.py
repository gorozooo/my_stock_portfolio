import json
from django.http import JsonResponse, HttpResponseBadRequest
from datetime import datetime, timezone, timedelta
from django.views.decorators.csrf import csrf_exempt
from . import _mock_data  # 既存の board_api 本文を別モジュール化してもOK
from advisor.models import ActionLog, Reminder

JST = timezone(timedelta(hours=9))

def board_api(request):
    # JSTの 07:25 生成相当（モック）
    now = datetime.now(timezone(timedelta(hours=9)))
    data = {
        "meta": {
            "generated_at": now.replace(hour=7, minute=25, second=0, microsecond=0).isoformat(),
            "model_version": "v0.1-mock",
            "adherence_week": 0.84,
            "regime": { "trend_prob": 0.63, "range_prob": 0.37, "nikkei": "↑", "topix": "→" }
        },
        "theme": {
            "week": "2025-W43",
            "top3": [
                {"id":"semiconductor","label":"半導体","score":0.78},
                {"id":"travel","label":"旅行","score":0.62},
                {"id":"banks","label":"銀行","score":0.41}
            ]
        },
        "highlights": [
            {
                "ticker":"8035.T","name":"東京エレクトロン","segment":"中期（20〜45日）",
                "action":"買い候補（勢い強）",
                "reasons":["半導体テーマが強い（78点）","出来高が増えている（+35%）","あなたの得意型（AI勝率82%）"],
                "targets":{"tp":"目標 +8〜12%","sl":"損切り -3%"},
                "ai":{"win_prob":0.82,"size_mult":1.08},
                "theme":{"id":"semiconductor","label":"半導体","score":0.78}
            },
            {
                "ticker":"7203.T","name":"トヨタ","segment":"中期（20〜45日）",
                "action":"30日目 → 一部売り",
                "reasons":["保有日数の区切り","自動車テーマ 65点","最近は横ばい"],
                "targets":{"tp":"前回高値付近で1/3売り","sl":"週の目安を割ったら縮小"},
                "ai":{"win_prob":0.64,"size_mult":0.96},
                "theme":{"id":"auto","label":"自動車","score":0.65}
            },
            {
                "ticker":"6758.T","name":"ソニーG","segment":"短期（5〜10日）",
                "action":"買い候補（短期の勢い）",
                "reasons":["出来高が増えている","戻りが強い","AI勝率74%"],
                "targets":{"tp":"+4〜6%で半分利確","sl":"-2%で撤退"},
                "ai":{"win_prob":0.74,"size_mult":1.05},
                "theme":{"id":"electronics","label":"電機","score":0.58}
            },
            {
                "ticker":"8267.T","name":"イオン","segment":"NISA（長期）",
                "action":"配当・優待目的で継続",
                "reasons":["決算前の確認","生活必需で安定","分散の役割"],
                "targets":{"tp":"長期保有が基本","sl":"想定範囲外なら縮小"},
                "ai":{"win_prob":0.60,"size_mult":1.00},
                "theme":{"id":"retail","label":"小売","score":0.55}
            },
            {
                "ticker":"8306.T","name":"三菱UFJ","segment":"中期（20〜45日）",
                "action":"買い候補（銀行）",
                "reasons":["銀行テーマ 41点（様子見寄り）","値動きは安定","分散の候補"],
                "targets":{"tp":"+5〜8%で段階利確","sl":"-3%で撤退"},
                "ai":{"win_prob":0.61,"size_mult":0.92},
                "theme":{"id":"banks","label":"銀行","score":0.41}
            }
        ]
    }
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    
@csrf_exempt
def record_action(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    try:
        payload = json.loads(request.body.decode("utf-8"))
        action = payload.get("action")
        ticker = payload.get("ticker","")
        policy_id = payload.get("policy_id","")
        note = payload.get("note","")
        log = ActionLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            ticker=ticker, policy_id=policy_id, action=action, note=note
        )
        return JsonResponse({"ok": True, "id": log.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@csrf_exempt
def create_reminder(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ticker = payload.get("ticker","")
        minutes = int(payload.get("after_minutes", 120))
        fire_at = datetime.now(JST) + timedelta(minutes=minutes)
        r = Reminder.objects.create(
            user=request.user if request.user.is_authenticated else None,
            ticker=ticker,
            message=f"{ticker} をもう一度チェック",
            fire_at=fire_at
        )
        return JsonResponse({"ok": True, "id": r.id, "fire_at": fire_at.isoformat()})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    