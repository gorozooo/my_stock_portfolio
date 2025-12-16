# aiapp/views/picks_debug.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from aiapp.services.behavior_banner_service import build_behavior_banner_summary

JST = timezone(timedelta(hours=9))

# 実行ディレクトリ依存を避けて BASE_DIR 基準に固定
PICKS_DIR = Path(settings.BASE_DIR) / "media" / "aiapp" / "picks"


# picks_debug.html 側で attribute アクセスしやすいように軽いラッパを用意
@dataclass
class PickDebugItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    # 追加：ランキング
    rank: Optional[int] = None

    # 追加：EV_true（証券会社別）
    ev_true_rakuten: Optional[float] = None
    ev_true_matsui: Optional[float] = None
    ev_true_sbi: Optional[float] = None

    # チャート用 OHLC
    chart_open: Optional[List[float]] = None
    chart_high: Optional[List[float]] = None
    chart_low: Optional[List[float]] = None
    chart_closes: Optional[List[float]] = None
    chart_dates: Optional[List[str]] = None  # "YYYY-MM-DD"

    # MA 系（5 / 25 / 75 / 100 / 200）
    chart_ma_5: Optional[List[float]] = None
    chart_ma_25: Optional[List[float]] = None
    chart_ma_75: Optional[List[float]] = None
    chart_ma_100: Optional[List[float]] = None
    chart_ma_200: Optional[List[float]] = None

    # VWAP / RSI
    chart_vwap: Optional[List[float]] = None
    chart_rsi: Optional[List[float]] = None

    # 52週・上場来 高安値（水平線用）
    hi_52w: Optional[float] = None
    lo_52w: Optional[float] = None
    hi_all_time: Optional[float] = None
    lo_all_time: Optional[float] = None

    last_close: Optional[float] = None
    atr: Optional[float] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    score: Optional[float] = None
    score_100: Optional[int] = None
    stars: Optional[int] = None

    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    qty_sbi: Optional[int] = None
    required_cash_sbi: Optional[float] = None
    est_pl_sbi: Optional[float] = None
    est_loss_sbi: Optional[float] = None

    # 合計系（ビュー側で計算して詰める）
    qty_total: Optional[int] = None
    pl_total: Optional[float] = None
    loss_total: Optional[float] = None

    # 理由系
    reasons_text: Optional[List[str]] = None
    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None
    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None
    reason_sbi: Optional[str] = None


# =========================================================
# ヘルパ
# =========================================================
def _normalize_str_list(v: Any) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else None
    if isinstance(v, (list, tuple)):
        out: List[str] = []
        for x in v:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out or None
    return None


def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _to_float_list(v: Any) -> Optional[List[float]]:
    if not isinstance(v, (list, tuple)):
        return None
    out: List[float] = []
    for x in v:
        try:
            out.append(float(x))
        except Exception:
            continue
    return out or None


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _safe_behavior_banner(days: int = 30) -> Dict[str, Any]:
    raw: Any = None
    try:
        raw = build_behavior_banner_summary(days=days)
    except Exception:
        raw = None

    banner: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    counts_raw: Any = banner.get("counts")
    counts: Dict[str, Any] = counts_raw if isinstance(counts_raw, dict) else {}

    evaluated = _safe_int(counts.get("evaluated"), 0)
    pending_future = _safe_int(counts.get("pending_future"), 0)
    skip = _safe_int(counts.get("skip"), 0)
    unknown = _safe_int(counts.get("unknown"), 0)

    total_raw = banner.get("total")
    total = _safe_int(total_raw, evaluated + pending_future + skip + unknown)

    return {
        "total": total,
        "counts": {
            "evaluated": evaluated,
            "pending_future": pending_future,
            "skip": skip,
            "unknown": unknown,
        },
    }


# =========================================================
# JSON ロード
# =========================================================
def _load_json(
    kind: str = "all",
) -> Tuple[Dict[str, Any], List[PickDebugItem], Optional[str], Optional[str]]:
    if kind == "top":
        filename = "latest_full.json"
    else:
        kind = "all"
        filename = "latest_full_all.json"

    path = PICKS_DIR / filename
    if not path.exists():
        return {}, [], None, str(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], None, str(path)

    meta = data.get("meta") or {}
    raw_items = data.get("items") or []

    items: List[PickDebugItem] = []

    for row in raw_items:
        if not isinstance(row, dict):
            continue

        try:
            # ----- 理由系 -----
            reason_lines = _normalize_str_list(row.get("reason_lines"))
            reasons_text = _normalize_str_list(row.get("reasons_text"))

            reason_concern_raw = row.get("reason_concern")
            reason_concern = str(reason_concern_raw).strip() if reason_concern_raw else None

            reason_rakuten_raw = row.get("reason_rakuten")
            reason_matsui_raw = row.get("reason_matsui")
            reason_sbi_raw = row.get("reason_sbi")

            reason_rakuten = str(reason_rakuten_raw).strip() if reason_rakuten_raw else None
            reason_matsui = str(reason_matsui_raw).strip() if reason_matsui_raw else None
            reason_sbi = str(reason_sbi_raw).strip() if reason_sbi_raw else None

            # ----- 数量・PL -----
            qty_rakuten = _to_int(row.get("qty_rakuten"))
            qty_matsui = _to_int(row.get("qty_matsui"))
            qty_sbi = _to_int(row.get("qty_sbi"))

            est_pl_rakuten = _to_float(row.get("est_pl_rakuten"))
            est_pl_matsui = _to_float(row.get("est_pl_matsui"))
            est_pl_sbi = _to_float(row.get("est_pl_sbi"))

            est_loss_rakuten = _to_float(row.get("est_loss_rakuten"))
            est_loss_matsui = _to_float(row.get("est_loss_matsui"))
            est_loss_sbi = _to_float(row.get("est_loss_sbi"))

            # ----- 追加：EV_true（証券会社別） -----
            ev_true_r = _to_float(row.get("ev_true_rakuten"))
            ev_true_m = _to_float(row.get("ev_true_matsui"))
            ev_true_s = _to_float(row.get("ev_true_sbi"))

            # ----- チャート用 OHLC -----
            chart_open = _to_float_list(row.get("chart_open"))
            chart_high = _to_float_list(row.get("chart_high"))
            chart_low = _to_float_list(row.get("chart_low"))
            chart_closes = _to_float_list(row.get("chart_closes") or row.get("chart_close"))
            chart_dates = _normalize_str_list(row.get("chart_dates"))

            # ----- MA / VWAP / RSI -----
            chart_ma_5 = _to_float_list(
                row.get("chart_ma_5") or
                row.get("chart_ma_short") or
                row.get("ma_5")
            )
            chart_ma_25 = _to_float_list(
                row.get("chart_ma_25") or
                row.get("chart_ma_mid") or
                row.get("ma_25")
            )
            chart_ma_75 = _to_float_list(row.get("chart_ma_75") or row.get("ma_75"))
            chart_ma_100 = _to_float_list(row.get("chart_ma_100") or row.get("ma_100"))
            chart_ma_200 = _to_float_list(row.get("chart_ma_200") or row.get("ma_200"))
            chart_vwap = _to_float_list(row.get("chart_vwap") or row.get("vwap"))
            chart_rsi = _to_float_list(row.get("chart_rsi") or row.get("rsi") or row.get("rsi14"))

            # ----- 52週 / 上場来 高安値 -----
            hi_52w = _to_float(row.get("hi_52w") or row.get("high_52w"))
            lo_52w = _to_float(row.get("lo_52w") or row.get("low_52w"))
            hi_all_time = _to_float(row.get("hi_all_time") or row.get("high_all"))
            lo_all_time = _to_float(row.get("lo_all_time") or row.get("low_all"))

            it = PickDebugItem(
                code=str(row.get("code") or ""),
                name=row.get("name") or row.get("name_norm") or None,
                sector_display=row.get("sector_display") or None,

                ev_true_rakuten=ev_true_r,
                ev_true_matsui=ev_true_m,
                ev_true_sbi=ev_true_s,

                chart_open=chart_open,
                chart_high=chart_high,
                chart_low=chart_low,
                chart_closes=chart_closes,
                chart_dates=chart_dates,
                chart_ma_5=chart_ma_5,
                chart_ma_25=chart_ma_25,
                chart_ma_75=chart_ma_75,
                chart_ma_100=chart_ma_100,
                chart_ma_200=chart_ma_200,
                chart_vwap=chart_vwap,
                chart_rsi=chart_rsi,
                hi_52w=hi_52w,
                lo_52w=lo_52w,
                hi_all_time=hi_all_time,
                lo_all_time=lo_all_time,
                last_close=_to_float(row.get("last_close")),
                atr=_to_float(row.get("atr")),
                entry=_to_float(row.get("entry")),
                tp=_to_float(row.get("tp")),
                sl=_to_float(row.get("sl")),
                score=_to_float(row.get("score")),
                score_100=_to_int(row.get("score_100")),
                stars=_to_int(row.get("stars")),
                qty_rakuten=qty_rakuten,
                required_cash_rakuten=_to_float(row.get("required_cash_rakuten")),
                est_pl_rakuten=est_pl_rakuten,
                est_loss_rakuten=est_loss_rakuten,
                qty_matsui=qty_matsui,
                required_cash_matsui=_to_float(row.get("required_cash_matsui")),
                est_pl_matsui=est_pl_matsui,
                est_loss_matsui=est_loss_matsui,
                qty_sbi=qty_sbi,
                required_cash_sbi=_to_float(row.get("required_cash_sbi")),
                est_pl_sbi=est_pl_sbi,
                est_loss_sbi=est_loss_sbi,
                reasons_text=reasons_text,
                reason_lines=reason_lines,
                reason_concern=reason_concern,
                reason_rakuten=reason_rakuten,
                reason_matsui=reason_matsui,
                reason_sbi=reason_sbi,
            )

            # ----- 合計値（ビュー側で計算） -----
            qr = qty_rakuten or 0
            qm = qty_matsui or 0
            qs = qty_sbi or 0
            if qr or qm or qs:
                it.qty_total = qr + qm + qs

            pl_r = est_pl_rakuten or 0.0
            pl_m = est_pl_matsui or 0.0
            pl_s = est_pl_sbi or 0.0
            if pl_r or pl_m or pl_s:
                it.pl_total = pl_r + pl_m + pl_s

            loss_r = est_loss_rakuten or 0.0
            loss_m = est_loss_matsui or 0.0
            loss_s = est_loss_sbi or 0.0
            if loss_r or loss_m or loss_s:
                it.loss_total = loss_r + loss_m + loss_s

            items.append(it)
        except Exception:
            continue

    # 追加：rank を必ず振る（表示の “-” を消す）
    for idx, it in enumerate(items, start=1):
        it.rank = idx

    # 更新日時ラベル（ファイルの mtime ベース）
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=JST)
        youbi = "月火水木金土日"[mtime.weekday()]
        updated_at_label = mtime.strftime(f"%Y年%m月%d日({youbi}) %H:%M")
    except Exception:
        updated_at_label = None

    return meta, items, updated_at_label, str(path)


# =========================================================
# ビュー
# =========================================================
@login_required
def picks_debug_view(request: HttpRequest) -> HttpResponse:
    kind = request.GET.get("kind", "all").lower()
    if kind not in ("all", "top"):
        kind = "all"

    meta, items, updated_at_label, source_file = _load_json(kind=kind)

    # ===== 総件数（ユニバース内） =====
    stockmaster_total = meta.get("stockmaster_total")
    universe_count = meta.get("universe_count")
    total = meta.get("total")
    master_total = stockmaster_total or universe_count or total or 0

    # ===== フィルタ別削除件数 =====
    raw_filter_stats = meta.get("filter_stats") or {}
    filter_stats_raw: Dict[str, int] = {}
    if isinstance(raw_filter_stats, dict):
        for k, v in raw_filter_stats.items():
            try:
                filter_stats_raw[str(k)] = int(v)
            except Exception:
                continue

    LABELS: Dict[str, str] = {
        "LOW_TURNOVER": "出来高が少なく除外",
        "TOO_VOLATILE": "価格変動が激しすぎて除外",
        "PUMP_STYLE": "急騰銘柄（仕手株疑い）で除外",
        "PRICE_ANOMALY": "価格が異常と判定され除外",
        "NO_PRICE": "価格データが取得できず除外",
        "SKIP": "その他の条件で除外",
        "filter_error": "フィルタ処理でエラー",
        "work_error": "銘柄処理中にエラー",
    }

    filter_stats_jp: Dict[str, int] = {}
    for code, cnt in filter_stats_raw.items():
        label = LABELS.get(code, f"その他（{code}）")
        filter_stats_jp[label] = filter_stats_jp.get(label, 0) + cnt

    # ===== 行動データ状況バナー =====
    behavior_banner = _safe_behavior_banner(days=30)

    ctx: Dict[str, Any] = {
        "meta": meta,
        "items": items,
        "updated_at_label": updated_at_label,
        "source_file": source_file,
        "filter_stats": filter_stats_jp,
        "master_total": master_total,
        "behavior_banner": behavior_banner,
    }
    return render(request, "aiapp/picks_debug.html", ctx)