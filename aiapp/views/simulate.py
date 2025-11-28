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
    - モード / 日付 / 銘柄コード・名称でフィルタ
    - 1日分だけ表示（最大100件）

    ★ 重複除外仕様
      「同じ銘柄・同じ内容のシミュレは、同じ日付内で重複させない」
      → 同じ日・同じ code・同じ mode・同じエントリー/数量/想定PL・想定損失・TP・SL は
         最初の1件だけ残し、以降は一覧から除外する。

    ★ スナップショット仕様
      - entry / tp / sl をその時点の値で固定保存
      - qty_rakuten / qty_matsui
      - est_pl_rakuten / est_loss_rakuten / est_pl_matsui / est_loss_matsui
      - price_date / ts など

    ★ KPI
      - summary_today: 今日の全ログベース（モードや画面フィルタとは無関係）
      - summary_total: 全期間の全ログベース（通算成績）
    """

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # ---- フィルタ値（クエリパラメータ） ------------------------------
    # mode: all / live / demo （一覧表示用のフィルタ）
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "live", "demo"):
        mode = "all"

    # 日付フィルタ: YYYY-MM-DD （一覧表示用）
    date_str_param = (request.GET.get("date") or "").strip()

    # q: 銘柄コード or 名称の部分一致（一覧表示用）
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

                # エントリー時刻 / エグジット時刻（sim_eval_service で付与）
                entry_dt = _parse_ts(rec.get("eval_entry_ts"))
                exit_dt = _parse_ts(rec.get("eval_exit_ts"))

                if entry_dt is not None:
                    rec["entry_dt"] = entry_dt
                    rec["entry_label"] = entry_dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["entry_dt"] = None
                    rec["entry_label"] = ""

                if exit_dt is not None:
                    rec["exit_dt"] = exit_dt
                    rec["exit_label"] = exit_dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["exit_dt"] = None
                    rec["exit_label"] = ""

                # exit_reason のラベル（評価結果テキスト用）
                exit_reason = rec.get("eval_exit_reason") or ""
                exit_reason_label = ""
                if exit_reason == "hit_tp":
                    exit_reason_label = "利確"
                elif exit_reason == "hit_sl":
                    exit_reason_label = "損切"
                elif exit_reason == "horizon_close":
                    # タイムアップ → 持ち越し に変更
                    exit_reason_label = "持ち越し"
                elif exit_reason in ("no_touch", "no_fill"):
                    exit_reason_label = "指値に一度も触れなかった"

                rec["exit_reason"] = exit_reason
                rec["exit_reason_label"] = exit_reason_label

                entries_all.append(rec)

    # ---- ts 降順でソート ----------------------------------------------
    def _sort_key(r: Dict[str, Any]):
        # _dt があればそれを優先、無ければ ts の文字列
        dt = r.get("_dt")
        if isinstance(dt, timezone.datetime):
            return dt
        return str(r.get("ts") or "")

    entries_all.sort(key=_sort_key, reverse=True)

    # ---- 同じ日・同じ内容の重複をまとめる ----------------------------
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
            continue

        seen_keys.add(key)
        deduped.append(e)

    entries_all = deduped

    # ---- id の付与（削除用の安定したインデックス） ------------------
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

    # ---- combined_label を付与（勝ち/負け/引き分け/見送り） ----------
    for e in entries_all:
        # 合計株数（0 の場合は完全に見送り）
        qty_total = 0.0
        for key in ("qty_rakuten", "qty_matsui"):
            v = e.get(key)
            try:
                if v is not None:
                    qty_total += float(v)
            except (TypeError, ValueError):
                pass

        label_r = e.get("eval_label_rakuten")
        label_m = e.get("eval_label_matsui")

        labels = set()
        for lb in (label_r, label_m):
            if isinstance(lb, str) and lb:
                labels.add(lb)

        combined = "skip"

        # 完全見送り（数量0 or ラベルなし／no_positionのみ）
        if qty_total == 0 or not labels or labels.issubset({"no_position"}):
            combined = "skip"
        elif "win" in labels:
            combined = "win"
        elif "lose" in labels:
            combined = "lose"
        elif "flat" in labels:
            combined = "flat"
        else:
            combined = "skip"

        e["combined_label"] = combined

    now = timezone.localtime()
    today_date = now.date()

    # ---- 日付候補リスト（トグル用） -----------------------------------
    date_set = set()
    for e in entries_all:
        dt = e.get("_dt")
        if isinstance(dt, timezone.datetime):
            date_set.add(dt.date())

    date_list = sorted(date_set, reverse=True)  # 新しい日付が先頭

    # 選択日付の決定
    selected_date: Optional[timezone.datetime.date] = None
    parsed_date: Optional[timezone.datetime.date] = None

    if date_str_param:
        try:
            parsed_date = timezone.datetime.strptime(date_str_param, "%Y-%m-%d").date()
        except Exception:
            parsed_date = None

    if parsed_date and parsed_date in date_set:
        selected_date = parsed_date
    else:
        if today_date in date_set:
            selected_date = today_date
        elif date_list:
            selected_date = date_list[0]
        else:
            selected_date = None

    selected_date_str = selected_date.isoformat() if selected_date else ""

    # テンプレ用の日付トグル候補（最大30日分）
    date_options: List[Dict[str, str]] = []
    for d in date_list[:30]:
        label = d.strftime("%Y-%m-%d")
        date_options.append({
            "value": d.isoformat(),
            "label": label,
        })

    # ---- KPI集計：今日 & 通算（フィルタとは無関係） -------------------
    def _accumulate(summary: Dict[str, Any], e: Dict[str, Any]) -> None:
        total_pl = summary.get("total_pl", 0.0)

        # 合計PL（楽天＋松井）
        for key in ("eval_pl_rakuten", "eval_pl_matsui"):
            val = e.get(key)
            try:
                if val is not None:
                    total_pl += float(val)
            except (TypeError, ValueError):
                pass

        summary["total_pl"] = total_pl

        combined = e.get("combined_label")
        if combined == "win":
            summary["win"] = summary.get("win", 0) + 1
        elif combined == "lose":
            summary["lose"] = summary.get("lose", 0) + 1
        elif combined == "flat":
            summary["flat"] = summary.get("flat", 0) + 1
        else:
            summary["skip"] = summary.get("skip", 0) + 1

    summary_today: Dict[str, Any] = {
        "win": 0,
        "lose": 0,
        "flat": 0,
        "skip": 0,
        "total_pl": 0.0,
        "has_data": False,
    }
    summary_total: Dict[str, Any] = {
        "win": 0,
        "lose": 0,
        "flat": 0,
        "skip": 0,
        "total_pl": 0.0,
        "has_data": False,
    }

    for e in entries_all:
        dt = e.get("_dt")
        if isinstance(dt, timezone.datetime) and dt.date() == today_date:
            _accumulate(summary_today, e)
        _accumulate(summary_total, e)

    summary_today["has_data"] = (summary_today["win"] +
                                 summary_today["lose"] +
                                 summary_today["flat"] +
                                 summary_today["skip"]) > 0
    summary_total["has_data"] = (summary_total["win"] +
                                 summary_total["lose"] +
                                 summary_total["flat"] +
                                 summary_total["skip"]) > 0

    # ---- 一覧表示用フィルタ（モード / 日付 / 検索） -------------------
    filtered: List[Dict[str, Any]] = []

    for e in entries_all:
        # 1) mode フィルタ（all / live / demo）
        rec_mode = (e.get("mode") or "").lower()
        if mode == "live" and rec_mode != "live":
            continue
        if mode == "demo" and rec_mode != "demo":
            continue
        # mode == "all" のときはスルー

        # 2) 日付フィルタ（選択日と _dt.date が一致するものだけ）
        dt: Optional[timezone.datetime] = e.get("_dt")
        if selected_date:
            if not isinstance(dt, timezone.datetime):
                continue
            if dt.date() != selected_date:
                continue

        # 3) 銘柄コード / 名称フィルタ
        if q:
            code = str(e.get("code") or "")
            name = str(e.get("name") or "")
            if q not in code and q not in name:
                continue

        filtered.append(e)

    # 最大100件に制限（1日20件程度想定だが安全のため）
    entries = filtered[:100]

    ctx = {
        "entries": entries,
        "mode": mode,
        "q": q,
        "selected_date": selected_date_str,
        "date_options": date_options,
        "summary_today": summary_today,
        "summary_total": summary_total,
    }
    return render(request, "aiapp/simulate_list.html", ctx)