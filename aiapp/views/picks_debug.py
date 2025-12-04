# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

JST = timezone(timedelta(hours=9))

PICKS_DIR = Path("media/aiapp/picks")


# picks_debug.html 側で attribute アクセスしやすいように軽いラッパを用意
@dataclass
class PickDebugItem:
    code: str
    name: str | None = None
    sector_display: str | None = None

    last_close: float | None = None
    atr: float | None = None

    entry: float | None = None
    tp: float | None = None
    sl: float | None = None

    score: float | None = None
    score_100: int | None = None
    stars: int | None = None

    qty_rakuten: int | None = None
    required_cash_rakuten: float | None = None
    est_pl_rakuten: float | None = None
    est_loss_rakuten: float | None = None

    qty_matsui: int | None = None
    required_cash_matsui: float | None = None
    est_pl_matsui: float | None = None
    est_loss_matsui: float | None = None


def _load_json(
    kind: str = "all",
) -> tuple[Dict[str, Any], List[PickDebugItem], str | None, str | None]:
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
            it = PickDebugItem(
                code=str(row.get("code") or ""),
                name=row.get("name") or row.get("name_norm") or None,
                sector_display=row.get("sector_display") or None,
                last_close=row.get("last_close"),
                atr=row.get("atr"),
                entry=row.get("entry"),
                tp=row.get("tp"),
                sl=row.get("sl"),
                score=row.get("score"),
                score_100=row.get("score_100"),
                stars=row.get("stars"),
                qty_rakuten=row.get("qty_rakuten"),
                required_cash_rakuten=row.get("required_cash_rakuten"),
                est_pl_rakuten=row.get("est_pl_rakuten"),
                est_loss_rakuten=row.get("est_loss_rakuten"),
                qty_matsui=row.get("qty_matsui"),
                required_cash_matsui=row.get("required_cash_matsui"),
                est_pl_matsui=row.get("est_pl_matsui"),
                est_loss_matsui=row.get("est_loss_matsui"),
            )
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

    # 総StockMaster件数（picks_build 側で universe_count として埋めている想定）
    master_total = meta.get("universe_count")
    if master_total is not None:
        # テンプレ側の {{ meta.stockmaster_total }} で拾えるようにコピー
        meta["stockmaster_total"] = master_total

    # フィルタ別削除件数（dict: reason_code -> count）
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
        "PRICE_ANOMALY": "価格が異常と判定され除外",
        "NO_PRICE": "価格データが取得できず除外",
        "SKIP": "その他の条件で除外",
        "filter_error": "フィルタ処理でエラー",
        "work_error": "銘柄処理中にエラー",
    }

    filter_stats_jp: Dict[str, int] = {}
    for code, cnt in filter_stats_raw.items():
        label = LABELS.get(code, f"その他（{code}）")
        # 同じラベルに複数コードがマップされても合算されるように
        filter_stats_jp[label] = filter_stats_jp.get(label, 0) + cnt

    ctx: Dict[str, Any] = {
        "meta": meta,
        "items": items,
        "updated_at_label": updated_at_label,
        "source_file": source_file,
        "filter_stats": filter_stats_jp,
    }
    return render(request, "aiapp/picks_debug.html", ctx)