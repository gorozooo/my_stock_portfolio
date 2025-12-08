# aiapp/views/picks_debug.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

JST = timezone(timedelta(hours=9))
PICKS_DIR = Path("media/aiapp/picks")


# picks_debug.html 側で attribute アクセスしやすいように軽いラッパを用意
@dataclass
class PickDebugItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    # チャート用 OHLC（picks_build からそのまま受け取る）
    chart_open: Optional[List[float]] = None
    chart_high: Optional[List[float]] = None
    chart_low: Optional[List[float]] = None
    chart_closes: Optional[List[float]] = None

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
    reasons_text: Optional[List[str]] = None          # sizing_service 側の共通メッセージ
    reason_lines: Optional[List[str]] = None          # reasons サービスの「選定理由」最大5行
    reason_concern: Optional[str] = None              # 懸念ポイント1行
    reason_rakuten: Optional[str] = None              # 楽天だけ0株の理由など
    reason_matsui: Optional[str] = None               # 松井だけ0株の理由など
    reason_sbi: Optional[str] = None                  # SBIだけ0株の理由など


def _normalize_str_list(v: Any) -> Optional[List[str]]:
    """
    JSON 側から来る文字列 or 配列を「文字列リスト」に正規化。
    """
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
    """
    chart_open / chart_high / chart_low / chart_closes 用。
    JSON から読み込んだ list を float list に正規化する。
    """
    if not isinstance(v, (list, tuple)):
        return None
    out: List[float] = []
    for x in v:
        try:
            out.append(float(x))
        except Exception:
            continue
    return out or None


def _load_json(
    kind: str = "all",
) -> Tuple[Dict[str, Any], List[PickDebugItem], Optional[str], Optional[str]]:
    """
    latest_full_all.json / latest_full.json を読み込んで
    (meta, items, updated_at_label, source_file) を返す。
    kind:
      "all" → latest_full_all.json
      "top" → latest_full.json
    """
    if kind == "top":
        filename = "latest_full.json"
    else:
        kind = "all"
        filename = "latest_full_all.json"

    path = PICKS_DIR / filename
    if not path.exists():
        # ファイルが無いときは空を返す
        return {}, [], None, str(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], None, str(path)

    meta = data.get("meta") or {}
    raw_items = data.get("items") or []

    items: List[PickDebugItem] = []

    for row in raw_items:
        # row は picks_build の asdict(PickItem) 相当の dict
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

            # ----- チャート用 OHLC -----
            chart_open = _to_float_list(row.get("chart_open"))
            chart_high = _to_float_list(row.get("chart_high"))
            chart_low = _to_float_list(row.get("chart_low"))
            chart_closes = _to_float_list(row.get("chart_closes"))

            it = PickDebugItem(
                code=str(row.get("code") or ""),
                name=row.get("name") or row.get("name_norm") or None,
                sector_display=row.get("sector_display") or None,
                chart_open=chart_open,
                chart_high=chart_high,
                chart_low=chart_low,
                chart_closes=chart_closes,
                last_close=row.get("last_close"),
                atr=row.get("atr"),
                entry=row.get("entry"),
                tp=row.get("tp"),
                sl=row.get("sl"),
                score=row.get("score"),
                score_100=row.get("score_100"),
                stars=row.get("stars"),
                qty_rakuten=qty_rakuten,
                required_cash_rakuten=row.get("required_cash_rakuten"),
                est_pl_rakuten=est_pl_rakuten,
                est_loss_rakuten=est_loss_rakuten,
                qty_matsui=qty_matsui,
                required_cash_matsui=row.get("required_cash_matsui"),
                est_pl_matsui=est_pl_matsui,
                est_loss_matsui=est_loss_matsui,
                qty_sbi=qty_sbi,
                required_cash_sbi=row.get("required_cash_sbi"),
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
            # 1行だけ壊れていても全体は落とさない
            continue

    # 更新日時ラベル（ファイルの mtime ベース）
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=JST)
        youbi = "月火水木金土日"[mtime.weekday()]
        updated_at_label = mtime.strftime(f"%Y年%m月%d日({youbi}) %H:%M")
    except Exception:
        updated_at_label = None

    return meta, items, updated_at_label, str(path)


@login_required
def picks_debug_view(request: HttpRequest) -> HttpResponse:
    """
    AI Picks 診断ビュー:
    picks_build が出力した JSON（latest_full_all / latest_full）をそのまま一覧表示。
    GET パラメータ:
      ?kind=all  … latest_full_all.json（デフォルト）
      ?kind=top  … latest_full.json
    """
    kind = request.GET.get("kind", "all").lower()
    if kind not in ("all", "top"):
        kind = "all"

    meta, items, updated_at_label, source_file = _load_json(kind=kind)

    # ===== 総件数（ユニバース内） =====
    stockmaster_total = meta.get("stockmaster_total")
    universe_count = meta.get("universe_count")
    total = meta.get("total")
    master_total = stockmaster_total or universe_count or total or 0

    # ===== フィルタ別削除件数（dict: reason_code -> count） =====
    raw_filter_stats = meta.get("filter_stats") or {}
    filter_stats_raw: Dict[str, int] = {}
    if isinstance(raw_filter_stats, dict):
        for k, v in raw_filter_stats.items():
            try:
                filter_stats_raw[str(k)] = int(v)
            except Exception:
                continue

    # 理由コード → 日本語ラベル
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

    ctx: Dict[str, Any] = {
        "meta": meta,
        "items": items,
        "updated_at_label": updated_at_label,
        "source_file": source_file,
        "filter_stats": filter_stats_jp,
        "master_total": master_total,
    }
    return render(request, "aiapp/picks_debug.html", ctx)