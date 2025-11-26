# aiapp/views/simulate.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    JSONL の ts(ISO文字列) を timezone-aware datetime に変換する。
    失敗した場合は None を返す。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


@login_required
def simulate_list(request: HttpRequest) -> HttpResponse:
    """
    AI Picks の「シミュレ」で登録した紙トレを一覧表示するビュー。

    - /media/aiapp/simulate/*.jsonl を全部読む
    - ログインユーザーの分だけ抽出
    - ts 降順でソート
    - mode / 期間 / 銘柄コード・名称でフィルタ
    - 最大100件まで表示

    ★ 追加仕様
      「同じ銘柄・同じ内容のシミュレは、同じ日付内で重複させない」
      → 同じ日・同じ code・同じ mode・同じエントリー/数量/想定PL・想定損失・TP・SL は
         最初の1件だけ残し、以降は一覧から除外する。

    ★ スナップショット仕様（レベル3前提）
      - entry / tp / sl をその時点の値で固定保存
      - qty_rakuten / qty_matsui
      - est_pl_rakuten / est_loss_rakuten / est_pl_matsui / est_loss_matsui
      - price_date / ts など
    """

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # ---- フィルタ値（クエリパラメータ） ------------------------------
    # mode: all / live / demo
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "live", "demo"):
        mode = "all"

    # period: today / 7d / 30d
    period = (request.GET.get("period") or "30d").lower()
    if period not in ("today", "7d", "30d"):
        period = "30d"

    # q: 銘柄コード or 名称の部分一致
    q = (request.GET.get("q") or "").strip()

    # ---- JSONL 読み込み ------------------------------------------------
    entries_all: List[Dict[str, Any]] = []

    if sim_dir.exists():
        # ファイル名順に読み込む（古い順）→ 後で ts でまとめてソート
        for path in sorted(sim_dir.glob("*.jsonl")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                # ユーザー別に絞る
                if rec.get("user_id") != user.id:
                    continue

                # ts を datetime + 表示ラベルに整形
                ts_str = rec.get("ts")
                dt = _parse_ts(ts_str)
                if dt is not None:
                    rec["_dt"] = dt
                    rec["ts_label"] = dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["_dt"] = None
                    rec["ts_label"] = ts_str or ""

                entries_all.append(rec)

    # ---- ts 降順でソート（従来通り）-----------------------------------
    def _sort_key(r: Dict[str, Any]):
        # _dt があればそれを優先、無ければ ts の文字列
        dt = r.get("_dt")
        if isinstance(dt, timezone.datetime):
            return dt
        return str(r.get("ts") or "")

    entries_all.sort(key=_sort_key, reverse=True)

    # ---- ★ 同じ日・同じ内容の重複をまとめる --------------------------
    #   「同じ銘柄の同じ内容は同日で重複しないようにする」
    #   → key: (日付, code, mode, entry, tp, sl,
    #           qty_rakuten, qty_matsui,
    #           est_pl_rakuten, est_pl_matsui,
    #           est_loss_rakuten, est_loss_matsui)
    deduped: List[Dict[str, Any]] = []
    seen_keys = set()

    for e in entries_all:
        dt = e.get("_dt")
        day = dt.date() if isinstance(dt, timezone.datetime) else None

        key = (
            day,
            e.get("code"),
            (e.get("mode") or "").lower() if e.get("mode") else None,
            e.get("entry"),
            e.get("tp"),
            e.get("sl"),
            e.get("qty_rakuten"),
            e.get("qty_matsui"),
            e.get("est_pl_rakuten"),
            e.get("est_pl_matsui"),
            e.get("est_loss_rakuten"),
            e.get("est_loss_matsui"),
        )

        if key in seen_keys:
            # 同じ日・同じ内容が既にあれば後ろの分は捨てる
            continue

        seen_keys.add(key)
        deduped.append(e)

    entries_all = deduped

    # ---- id の付与（削除用の安定したインデックス） ------------------
    # 既に int の id があればそのまま使い、無ければ 0,1,2,… を振る。
    for idx, e in enumerate(entries_all):
        eid = e.get("id")
        if isinstance(eid, int):
            continue
        try:
            if isinstance(eid, str) and eid.strip() != "":
                e["id"] = int(eid)
                continue
        except Exception:
            pass
        e["id"] = idx

    # ---- フィルタ適用 --------------------------------------------------
    now = timezone.localtime()
    filtered: List[Dict[str, Any]] = []

    for e in entries_all:
        # 1) mode フィルタ
        rec_mode = (e.get("mode") or "").lower()
        if mode == "live" and rec_mode != "live":
            continue
        if mode == "demo" and rec_mode != "demo":
            continue
        # mode == "all" のときはスルー

        # 2) 期間フィルタ
        dt: Optional[timezone.datetime] = e.get("_dt")
        if period == "today":
            if not isinstance(dt, timezone.datetime):
                continue
            if dt.date() != now.date():
                continue
        elif period == "7d":
            if not isinstance(dt, timezone.datetime):
                continue
            if dt < now - timezone.timedelta(days=7):
                continue
        elif period == "30d":
            if not isinstance(dt, timezone.datetime):
                continue
            if dt < now - timezone.timedelta(days=30):
                continue
        # ※ 将来 "all" を作る場合はここに分岐追加

        # 3) 銘柄コード / 名称フィルタ
        if q:
            code = str(e.get("code") or "")
            name = str(e.get("name") or "")
            if q not in code and q not in name:
                continue

        filtered.append(e)

    # 最大100件に制限
    entries = filtered[:100]

    ctx = {
        "entries": entries,
        "mode": mode,
        "period": period,
        "q": q,
    }
    return render(request, "aiapp/simulate_list.html", ctx)