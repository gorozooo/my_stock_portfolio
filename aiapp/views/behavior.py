from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


# =========================
# helpers
# =========================

def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except Exception:
                continue
    except Exception:
        return rows
    return rows


def _summarize_simulate_dir(sim_dir: Path) -> Dict[str, Any]:
    """
    media/aiapp/simulate/sim_orders_*.jsonl を集計する。
    qty_pro がある行のみ対象。
    eval_label_pro がある行は「評価済み」扱い。
    """
    paths = sorted(glob.glob(str(sim_dir / "sim_orders_*.jsonl")))
    total_qty = 0
    eval_done = 0
    wl = 0
    labels: Dict[str, int] = {"win": 0, "lose": 0, "carry": 0, "skip": 0, "flat": 0, "other": 0}

    for p in paths:
        for r in _read_jsonl(Path(p)):
            if not r.get("qty_pro"):
                continue
            total_qty += 1
            lab = r.get("eval_label_pro")
            if lab is None:
                continue
            eval_done += 1
            lab_s = str(lab).strip().lower()
            if lab_s in ("win", "lose"):
                wl += 1
            if lab_s in labels:
                labels[lab_s] += 1
            else:
                labels["other"] += 1

    return {
        "files": len(paths),
        "total_qty": total_qty,
        "eval_done": eval_done,
        "wl": wl,
        "labels": labels,
    }


def _understanding_label(wl_total: int) -> str:
    if wl_total <= 1:
        return "ZERO"
    if wl_total <= 3:
        return "LOW"
    if wl_total <= 8:
        return "MID"
    if wl_total <= 20:
        return "HIGH"
    return "DEEP"


def _make_hypotheses(
    wl_total: int,
    win_rate: Optional[float],
    avg_r: Optional[float],
    avg_pl: Optional[float],
    labels: Dict[str, int],
) -> List[str]:
    hyps: List[str] = []

    # まずは “今の事実” から始める（奇抜だけど嘘はつかない）
    if wl_total < 5:
        hyps.append("まだサンプルが少ない。いまは『クセの芽』だけ見えている段階。")
    else:
        hyps.append("勝敗が付いたデータが蓄積し始め、AIが“再現パターン”を作り始めています。")

    # 勝率
    if win_rate is not None:
        if win_rate >= 60:
            hyps.append("いまは『当てに行く』局面で強い。手数を増やしても崩れにくい可能性。")
        elif win_rate >= 45:
            hyps.append("勝率は中間帯。改善余地は『負けの質（損切りの形）』側に寄っている。")
        else:
            hyps.append("勝率がまだ低め。『入り方』より『切り方（撤退ルール）』に歪みがある可能性。")

    # R
    if avg_r is not None:
        if avg_r >= 0.3:
            hyps.append("平均Rがプラス。『勝ちのときに伸ばせる』癖が出ている。")
        elif avg_r >= 0:
            hyps.append("平均Rはほぼゼロ付近。ルール通りに切れているが、伸びも取り切れていない。")
        else:
            hyps.append("平均Rがマイナス。負けが“ルール想定より深く”なっている疑い（SLの滑り/我慢/ロット）。")

    # PL
    if avg_pl is not None:
        if avg_pl >= 0:
            hyps.append("平均PLはプラス。現時点では『微勝ちを積む』より『大負けを避ける』のが次の課題。")
        else:
            hyps.append("平均PLはマイナス。改善ポイントは『負けの頻度』より『負けの平均サイズ』側に寄る。")

    # carry/skipの比率（行動の温度感）
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    wl = int(labels.get("win", 0)) + int(labels.get("lose", 0))
    if (carry + skip) > wl and (carry + skip) >= 3:
        hyps.append("carry/skip が多い。『意思決定が慎重』か『条件が厳しすぎる』どちらかに寄っている。")

    # 多すぎたら絞る
    return hyps[:5]


def _make_notes(wl_total: int, sim_total: int, eval_done: int, labels: Dict[str, int]) -> List[str]:
    notes: List[str] = []
    notes.append(f"simulate（qty_proあり）={sim_total} 件 / 評価済み={eval_done} 件 / win-lose={wl_total} 件")
    if int(labels.get("carry", 0)) > 0:
        notes.append(f"carry={labels.get('carry', 0)} は“保有継続”なので、学習の勝敗データには入れていません。")
    if int(labels.get("skip", 0)) > 0:
        notes.append(f"skip={labels.get('skip', 0)} は“ポジション無し”なので、勝率には入れていません。")
    return notes[:4]


def _make_bias_map(
    win_rate: Optional[float],
    avg_r: Optional[float],
    labels: Dict[str, int],
) -> List[Dict[str, str]]:
    """
    “奇抜だけど実用的” を狙った、短い診断ラベル。
    """
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    wl = int(labels.get("win", 0)) + int(labels.get("lose", 0))

    # 行動温度
    if wl == 0:
        tempo = "未判定"
    else:
        if (carry + skip) >= wl:
            tempo = "慎重（見送り/継続多め）"
        else:
            tempo = "実行寄り（勝負回数多め）"

    # リスク感
    if avg_r is None:
        risk = "未判定"
    else:
        if avg_r >= 0.3:
            risk = "リスク回収が上手い"
        elif avg_r >= 0:
            risk = "概ねルール通り"
        else:
            risk = "損失側が重い"

    # 命中度
    if win_rate is None:
        hit = "未判定"
    else:
        if win_rate >= 60:
            hit = "命中高め"
        elif win_rate >= 45:
            hit = "平均帯"
        else:
            hit = "命中低め"

    return [
        {"name": "行動温度", "value": tempo},
        {"name": "撤退の質", "value": risk},
        {"name": "命中度", "value": hit},
    ]


def _make_wanted(
    wl_total: int,
    labels: Dict[str, int],
    avg_r: Optional[float],
) -> List[str]:
    wanted: List[str] = []
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))

    if wl_total < 8:
        wanted.append("同一ルール・同一モードでの連続トレード（WLを増やす）")
    if avg_r is not None and avg_r < 0:
        wanted.append("負けた日の次の1手（負け後にどう動くか）")
    if carry >= 2:
        wanted.append("carry の最終着地（利確/損切り/時間切れ）を増やす")
    if skip >= 2:
        wanted.append("見送った理由（なぜ入らなかったか）をメモに残すと学習が速い")
    if not wanted:
        wanted.append("今のパターンをもう一周（同条件で再現できるか）")
    return wanted[:5]


# =========================
# view
# =========================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ PRO仕様の行動ダッシュボード
    - simulate/*.jsonl を集計（carry/skipも含めて行動量を見る）
    - behavior/model/latest_behavior_model_u{user}.json を読む（学習結果）
    - has_data は「WLが1件以上」 or 「simulateが評価済み1件以上」で True にする
    """
    user = request.user
    today = timezone.localdate()
    today_label = today.strftime("%Y-%m-%d")

    base = Path(getattr(request, "META", {}).get("BASE_DIR", ""))  # 使わない（保険）
    media_root = Path("media")  # このプロジェクトは相対で動いてる前提（あなたの運用に合わせる）
    sim_dir = media_root / "aiapp" / "simulate"
    beh_dir = media_root / "aiapp" / "behavior"
    model_path = beh_dir / "model" / f"latest_behavior_model_u{user.id}.json"

    sim_sum = _summarize_simulate_dir(sim_dir)

    # 学習モデル（あれば）
    model_json = _read_json(model_path) or {}
    has_model = bool(model_json)

    # モデルから主要値
    win_rate = _safe_float(model_json.get("win_rate"))
    avg_pl = _safe_float(model_json.get("avg_pl"))
    avg_r = _safe_float(model_json.get("avg_r"))
    total_trades = int(model_json.get("total_trades") or 0)

    # WL総数は、simulate集計を優先（PRO評価をsimulateへ反映してるので）
    wl_total = int(sim_sum.get("wl") or 0)
    sim_total = int(sim_sum.get("total_qty") or 0)
    eval_done = int(sim_sum.get("eval_done") or 0)
    labels = sim_sum.get("labels") or {}

    # has_data判定：WLが1件以上 or 評価済みが1件以上
    has_data = (wl_total >= 1) or (eval_done >= 1)

    # さらに DB が空でもページは出してOK（実運用はファイル主導）
    # ただし、完全に何も無いなら empty
    if not has_data:
        return render(
            request,
            "aiapp/behavior_dashboard.html",
            {
                "has_data": False,
                "today_label": today_label,
                "sim_files": sim_sum.get("files", 0),
                "model": {"has_model": has_model},
            },
        )

    understanding_label = _understanding_label(wl_total)

    hypotheses = _make_hypotheses(
        wl_total=wl_total,
        win_rate=win_rate,
        avg_r=avg_r,
        avg_pl=avg_pl,
        labels=labels,
    )
    notes = _make_notes(wl_total=wl_total, sim_total=sim_total, eval_done=eval_done, labels=labels)
    bias_map = _make_bias_map(win_rate=win_rate, avg_r=avg_r, labels=labels)
    wanted = _make_wanted(wl_total=wl_total, labels=labels, avg_r=avg_r)

    # “total” は画面で使う用：simulate件数（qty_proあり）を採用
    ctx = {
        "has_data": True,
        "today_label": today_label,
        "sim_files": int(sim_sum.get("files") or 0),
        "total": sim_total,
        "sim_total": sim_total,
        "wl_total": wl_total,
        "win_rate": win_rate,
        "avg_pl": avg_pl,
        "avg_r": avg_r,
        "understanding_label": understanding_label,
        "hypotheses": hypotheses,
        "notes": notes,
        "bias_map": bias_map,
        "wanted": wanted,
        "model": {
            "has_model": has_model,
            "total_trades": total_trades,
        },
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)