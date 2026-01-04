from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
from django.http import JsonResponse, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth import get_user_model

from advisor.models_order import OrderMemo

# --- JPX銘柄名（data/tse_list.json） ---
_TSE: Dict[str, Dict[str, str]] = {}
def _load_tse():
    global _TSE
    if _TSE:
        return
    base = os.getcwd()
    path = os.path.join(base, "data", "tse_list.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _TSE = json.load(f) or {}
        except Exception:
            _TSE = {}

def _jpx_name(ticker: str, fallback: Optional[str] = None) -> str:
    _load_tse()
    t = (ticker or "").upper().strip()
    if t.endswith(".T"):
        t = t[:-2]
    rec = _TSE.get(t) or {}
    nm = (rec.get("name") or "").strip()
    return nm or (fallback or t)

def _yen(n: Optional[int]) -> str:
    return "—" if n is None else f"¥{n:,}"

def _actor(request: HttpRequest):
    U = get_user_model()
    u = getattr(request, "user", None)
    if u and u.is_authenticated:
        return u
    return U.objects.first()  # シングルユーザー想定のフォールバック

@login_required(login_url="/accounts/login/")
@require_GET
def memo_page(request: HttpRequest) -> HttpResponse:
    # 初回描画は空 → JSがAPIで取得して描画
    return render(request, "advisor/memos.html", {})

@require_GET
def memos_list_api(request: HttpRequest) -> JsonResponse:
    user = _actor(request)
    qs = OrderMemo.objects.filter(user=user).order_by("-created_at")[:200]
    items: List[Dict[str, Any]] = []
    for m in qs:
        jp = _jpx_name(m.ticker, m.name or None)
        items.append({
            "id": m.id,
            "ticker": (m.ticker or "").upper(),
            "name": jp,
            "display": f"{jp} ({(m.ticker or '').upper()})",
            "entry": m.entry_price,
            "tp": m.tp_price,
            "sl": m.sl_price,
            "window": m.window,
            "created_at": m.created_at.isoformat(),
        })
    return JsonResponse({"ok": True, "items": items})

@require_POST
def memo_delete_api(request: HttpRequest, pk: int) -> JsonResponse:
    user = _actor(request)
    obj = get_object_or_404(OrderMemo, pk=pk, user=user)
    obj.delete()
    return JsonResponse({"ok": True, "deleted": pk})