# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import timedelta
from typing import Dict

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.utils import timezone

from ..models_advisor import AdviceSession


# --------- 共通ユーティリティ（簡易アウトカム推定） ----------
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _improve_between(k0: Dict, k1: Dict) -> float:
    """
    KPI 改善度を -1.0〜+1.0 で返す簡易スコア。
      + ROI_eval_pct   … 上がるほど良い
      + liquidity_rate … 上がるほど良い
      + margin_ratio   … 下がるほど良い（符号を反転）
    正規化はざっくり（±50/±40/±40）でクリップ。
    """
    if not k0 or not k1:
        return 0.0
    d_roi = _safe_float(k1.get("roi_eval_pct")) - _safe_float(k0.get("roi_eval_pct"))
    d_liq = _safe_float(k1.get("liquidity_rate_pct")) - _safe_float(k0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(k0.get("margin_ratio_pct")) - _safe_float(k1.get("margin_ratio_pct"))  # 低いほど◎

    def clip(x, s):
        if not s:
            return 0.0
        return max(-1.0, min(1.0, x / s))

    return (clip(d_roi, 50.0) + clip(d_liq, 40.0) + clip(d_mrg, 40.0)) / 3.0


# --------- ① ワンクリで A/B を固定する（7日） ----------
def set_variant(request: HttpRequest, v: str) -> HttpResponse:
    v = (v or "").upper()
    if v not in ("A", "B"):
        return HttpResponseBadRequest("variant must be A or B")
    resp = redirect("ab_dashboard")  # ダッシュボードへ戻す
    resp.set_cookie("ab_variant", v, max_age=7 * 24 * 60 * 60, samesite="Lax")
    return resp


# --------- ② ブラウザで A/B 集計を確認（スマホ最適テンプレ対応） ----------
def ab_dashboard(request: HttpRequest) -> HttpResponse:
    """
    テンプレ ab_dashboard.html に合わせたコンテキストを返す。
      - a / b: { sessions, advices, taken, take_rate, avg_improve }（文字列は小数3桁）
      - base_days: 集計対象の過去日数
      - horizon_days: 何日後セッションで改善を見るか
      - now_str: ヘッダ表示用の現在時刻
    """
    # クエリで A/B 固定を切り替えられるように（スマホからの簡易操作）
    q_set = (request.GET.get("set") or "").upper()
    if q_set in ("A", "B"):
        resp = redirect("ab_dashboard")
        resp.set_cookie("ab_variant", q_set, max_age=7 * 24 * 60 * 60, samesite="Lax")
        return resp

    # パラメータ
    horizon_days = int(request.GET.get("horizon", "7"))
    base_days = int(request.GET.get("since", "90"))
    cutoff = timezone.now() - timedelta(days=base_days)

    # セッション（最近 base_days のみ）
    sessions = list(
        AdviceSession.objects.filter(created_at__gte=cutoff)
        .order_by("created_at")
    )

    # 集計用バケット
    stats = {
        "A": {"sessions": 0, "advices": 0, "taken": 0, "improve_sum": 0.0, "improve_n": 0},
        "B": {"sessions": 0, "advices": 0, "taken": 0, "improve_sum": 0.0, "improve_n": 0},
    }

    # i 番目のセッションから horizon_days 先の最初のセッションを探す
    def find_future(idx: int):
        base = sessions[idx]
        target = base.created_at + timedelta(days=horizon_days)
        for j in range(idx + 1, len(sessions)):
            if sessions[j].created_at >= target:
                return sessions[j]
        return None

    for i, s in enumerate(sessions):
        v = (getattr(s, "variant", None) or "A").upper()
        if v not in stats:
            v = "A"  # 想定外はAに寄せる

        stats[v]["sessions"] += 1
        items_qs = getattr(s, "items", None)
        items = list(items_qs.all()) if items_qs is not None else []
        stats[v]["advices"] += len(items)
        stats[v]["taken"] += sum(1 for it in items if getattr(it, "taken", False))

        fut = find_future(i)
        if fut:
            sc = _improve_between(s.context_json or {}, fut.context_json or {})
            stats[v]["improve_sum"] += float(sc)
            stats[v]["improve_n"] += 1

    # 表示整形（テンプレが期待するキー名にそろえる）
    def _fmt(bucket: dict) -> dict:
        adv = int(bucket["advices"])
        taken = int(bucket["taken"])
        tr = (taken / adv) if adv > 0 else 0.0
        n = int(bucket["improve_n"])
        ai = (bucket["improve_sum"] / n) if n > 0 else 0.0
        return {
            "sessions": int(bucket["sessions"]),
            "advices": adv,
            "taken": taken,
            "take_rate": f"{tr:.3f}",
            "avg_improve": f"{ai:.3f}",
        }

    a = _fmt(stats["A"])
    b = _fmt(stats["B"])

    ctx = dict(
        a=a,
        b=b,
        base_days=base_days,
        horizon_days=horizon_days,
        now_str=timezone.now().strftime("%Y年%m月%d日 %H:%M"),
    )
    return render(request, "advisor_ab.html", ctx)