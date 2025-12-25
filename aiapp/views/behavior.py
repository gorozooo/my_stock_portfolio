from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

# NOTE: VirtualTrade を import（既存通り）
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


def _streak_from_labels(wl_labels: List[str]) -> Tuple[str, int]:
    """
    wl_labels は古→新 の win/lose/flat 配列。
    戻り: (streak_label, streak_len)
    """
    if not wl_labels:
        return ("none", 0)
    last = wl_labels[-1]
    n = 1
    for i in range(len(wl_labels) - 2, -1, -1):
        if wl_labels[i] == last:
            n += 1
        else:
            break
    return (last, n)


def _make_sequence(wl_labels: List[str]) -> List[Dict[str, str]]:
    """
    表示用：古→新
    """
    seq: List[Dict[str, str]] = []
    for lab in wl_labels:
        lab2 = lab if lab in ("win", "lose", "flat") else "flat"
        txt = "W" if lab2 == "win" else ("L" if lab2 == "lose" else "F")
        seq.append({"label": lab2, "text": txt})
    return seq[-12:]


def _make_hypotheses(
    wl_total: int,
    win_rate: Optional[float],
    avg_r: Optional[float],
    avg_pl: Optional[float],
    labels: Dict[str, int],
    streak_label: str,
    streak_len: int,
) -> List[str]:
    hyps: List[str] = []

    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    win = int(labels.get("win", 0))
    lose = int(labels.get("lose", 0))

    if wl_total < 5:
        hyps.append("私はまだ“あなたの型”を確定できない。いまは《クセの芽》だけを保存している。")
    else:
        hyps.append("私は“あなたの型”を作り始めた。次は《再現できる勝ち方》だけを残していく。")

    if win_rate is not None:
        if win_rate >= 60:
            hyps.append("命中は高い。問題が起きるなら《勝ちを小さく》《負けを大きく》する癖の方。")
        elif win_rate >= 45:
            hyps.append("命中は平均帯。改善は《入り方》より《撤退の形》に寄る。")
        else:
            hyps.append("命中がまだ低い。選別ロジックが強すぎるか、刺さる条件がズレている。")

    if avg_r is not None:
        if avg_r >= 0.3:
            hyps.append("平均Rはプラス。私は《利確の形》を真似し始めていい段階。")
        elif avg_r >= 0:
            hyps.append("平均Rはゼロ付近。ルール順守はできているが、“伸ばす学習”が不足している。")
        else:
            hyps.append("平均Rがマイナス。負けがルール想定より深い。ロット/滑り/我慢のどれかが混ざっている。")

    if avg_pl is not None:
        if avg_pl >= 0:
            hyps.append("平均PLはプラス。次の敵は《大負け》ではなく《取りこぼし》の方に移る。")
        else:
            hyps.append("平均PLはマイナス。勝率より先に《負けの平均サイズ》を潰すと立て直しが速い。")

    if (carry + skip) >= (win + lose) and (carry + skip) >= 3:
        hyps.append("carry/skip が多い。あなたは“撃つ”より“様子を見る”で世界を制御している。条件が厳しすぎる可能性。")

    if streak_len >= 2 and streak_label in ("win", "lose"):
        if streak_label == "win":
            hyps.append(f"直近は WIN が {streak_len} 連続。私は“勝てる条件”を固定し、同条件だけを増殖させたい。")
        else:
            hyps.append(f"直近は LOSE が {streak_len} 連続。私は“負けの型”を逆に固定して、そこだけ入らないようにしたい。")

    return hyps[:6]


def _make_notes(wl_total: int, sim_total: int, eval_done: int, labels: Dict[str, int]) -> List[str]:
    notes: List[str] = []
    notes.append(f"simulate（qty_proあり）={sim_total} 件 / 評価済み={eval_done} 件 / win-lose={wl_total} 件")
    if int(labels.get("carry", 0)) > 0:
        notes.append(f"carry={labels.get('carry', 0)} は“保有継続”なので、勝率の母数には入れていません。")
    if int(labels.get("skip", 0)) > 0:
        notes.append(f"skip={labels.get('skip', 0)} は“ポジション無し”なので、勝率の母数には入れていません。")
    return notes[:4]


def _make_bias_map(
    win_rate: Optional[float],
    avg_r: Optional[float],
    labels: Dict[str, int],
) -> List[Dict[str, str]]:
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    wl = int(labels.get("win", 0)) + int(labels.get("lose", 0))

    if wl == 0:
        tempo = "未判定"
    else:
        tempo = "冷（見送り/継続多め）" if (carry + skip) >= wl else "熱（実行多め）"

    if avg_r is None:
        risk = "未判定"
    else:
        if avg_r >= 0.3:
            risk = "撤退は良い/回収も良い"
        elif avg_r >= 0:
            risk = "撤退は概ねルール通り"
        else:
            risk = "撤退が深い（要修正）"

    if win_rate is None:
        hit = "未判定"
    else:
        if win_rate >= 60:
            hit = "命中高め"
        elif win_rate >= 45:
            hit = "平均帯"
        else:
            hit = "命中低め"

    if (carry + skip) == 0:
        indecision = "未判定"
    else:
        indecision = "見送り優位（慎重）" if skip >= carry else "継続優位（粘る）"

    return [
        {"name": "行動温度", "value": tempo},
        {"name": "撤退の質", "value": risk},
        {"name": "命中度", "value": hit},
        {"name": "迷いの形", "value": indecision},
    ]


def _make_wanted(
    wl_total: int,
    labels: Dict[str, int],
    avg_r: Optional[float],
    streak_label: str,
    streak_len: int,
) -> List[str]:
    wanted: List[str] = []
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))

    if wl_total < 8:
        wanted.append("同一ルール・同一モードでの連続トレード（WLを増やす）")
    if avg_r is not None and avg_r < 0:
        wanted.append("負けの直後の次の一手（負け→取り返しに行く癖があるか）")
    if streak_len >= 2 and streak_label == "lose":
        wanted.append("LOSE連続中の条件を固定して“入らないルール”を作る（NGパターン抽出）")
    if carry >= 2:
        wanted.append("carry の最終着地（利確/損切り/時間切れ）を増やす")
    if skip >= 2:
        wanted.append("見送った理由（なぜ入らなかったか）をメモに残すと学習が速い")
    if not wanted:
        wanted.append("今の勝ちパターンをもう一周（同条件で再現できるか）")
    return wanted[:6]


def _extract_recent_trades(side_rows: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in side_rows[-limit:]:
        code = str(r.get("code") or "")
        label = str(r.get("eval_label") or "").strip().lower()
        pl = _safe_float(r.get("eval_pl")) or 0.0
        rv = _safe_float(r.get("eval_r"))
        mode = str(r.get("mode") or "").strip().lower()
        broker = str(r.get("broker") or "pro").strip().lower()
        ts = str(r.get("ts") or r.get("trade_date") or "")

        meta_bits: List[str] = []
        if broker:
            meta_bits.append(broker.upper())
        if mode:
            meta_bits.append(mode.upper())
        if ts:
            meta_bits.append(ts)
        meta = " / ".join(meta_bits) if meta_bits else "-"

        out.append(
            {
                "code": code,
                "label": label if label in ("win", "lose", "flat") else "flat",
                "pl": float(pl),
                "r": rv,
                "meta": meta,
            }
        )
    out.reverse()
    return out


def _load_ticker(ticker_path: Path) -> Dict[str, Any]:
    """
    media/aiapp/behavior/ticker/latest_ticker_u{user}.json
    無ければ空。
    """
    j = _read_json(ticker_path)
    if not j:
        return {"date": "", "lines": []}
    lines = j.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    lines = [str(x) for x in lines if str(x).strip()]
    return {
        "date": str(j.get("date") or ""),
        "lines": lines[:8],
    }


def _normalize_reason(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "(未設定)"
    return s


def _build_entry_reason_stats_from_side(side_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    behavior/latest_behavior_side.jsonl を元に entry_reason 別の集計を作る。
    side側は「評価が確定している」前提なので、まずここで可視化を成立させる。
    """
    agg_trials: Dict[str, int] = defaultdict(int)
    agg_wins: Dict[str, int] = defaultdict(int)
    agg_sum_r: Dict[str, float] = defaultdict(float)
    agg_cnt_r: Dict[str, int] = defaultdict(int)
    agg_sum_pl: Dict[str, float] = defaultdict(float)
    agg_cnt_pl: Dict[str, int] = defaultdict(int)

    for r in side_rows:
        lab = str(r.get("eval_label") or "").strip().lower()
        if lab not in ("win", "lose", "flat"):
            continue

        reason = _normalize_reason(str(r.get("entry_reason") or r.get("entry_reason_pro") or ""))

        agg_trials[reason] += 1
        if lab == "win":
            agg_wins[reason] += 1

        rv = _safe_float(r.get("eval_r"))
        if rv is not None:
            agg_sum_r[reason] += float(rv)
            agg_cnt_r[reason] += 1

        plv = _safe_float(r.get("eval_pl"))
        if plv is not None:
            agg_sum_pl[reason] += float(plv)
            agg_cnt_pl[reason] += 1

    out: List[Dict[str, Any]] = []
    for reason, trials in agg_trials.items():
        wins = agg_wins.get(reason, 0)
        win_rate = (wins / trials * 100.0) if trials > 0 else 0.0
        avg_r = (agg_sum_r[reason] / agg_cnt_r[reason]) if agg_cnt_r.get(reason, 0) > 0 else None
        avg_pl = (agg_sum_pl[reason] / agg_cnt_pl[reason]) if agg_cnt_pl.get(reason, 0) > 0 else None
        out.append(
            {
                "reason": reason,
                "trials": trials,
                "wins": wins,
                "win_rate": win_rate,
                "avg_r": avg_r,
                "avg_pl": avg_pl,
            }
        )

    out.sort(key=lambda x: (-int(x["trials"]), -float(x["win_rate"])))
    return out


def _extract_feature_importance(model_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    モデルJSONの中に feature importance があれば上位だけ表示用に抜く。
    対応パターン（どれかに当たればOK）:
    - {"feature_importance": {"featA":0.12, ...}}
    - {"feature_importance": [{"feature":"featA","importance":0.12}, ...]}
    - {"features": [{"name":"featA","importance":0.12}, ...]}
    """
    if not isinstance(model_json, dict):
        return []

    cand = model_json.get("feature_importance")
    out: List[Tuple[str, float]] = []

    if isinstance(cand, dict):
        for k, v in cand.items():
            fv = _safe_float(v)
            if fv is None:
                continue
            out.append((str(k), float(fv)))

    if isinstance(cand, list):
        for it in cand:
            if not isinstance(it, dict):
                continue
            name = it.get("feature") or it.get("name")
            imp = it.get("importance") or it.get("gain") or it.get("weight")
            if not name:
                continue
            fv = _safe_float(imp)
            if fv is None:
                continue
            out.append((str(name), float(fv)))

    cand2 = model_json.get("features")
    if isinstance(cand2, list) and not out:
        for it in cand2:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("feature")
            imp = it.get("importance") or it.get("gain") or it.get("weight")
            if not name:
                continue
            fv = _safe_float(imp)
            if fv is None:
                continue
            out.append((str(name), float(fv)))

    out.sort(key=lambda x: x[1], reverse=True)
    top = out[:6]
    return [{"name": n, "importance": imp} for n, imp in top]


def _extract_ml_numbers(model_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    初心者向けに「数値で説明できるもの」を拾う（無いキーは無視）。
    """
    if not isinstance(model_json, dict):
        return []

    def pick(*keys: str) -> Optional[float]:
        for k in keys:
            if k in model_json:
                v = _safe_float(model_json.get(k))
                if v is not None:
                    return v
        return None

    def pick_str(*keys: str) -> Optional[str]:
        for k in keys:
            if k in model_json:
                s = model_json.get(k)
                if s is None:
                    continue
                ss = str(s).strip()
                if ss:
                    return ss
        return None

    nums: List[Dict[str, Any]] = []

    total = model_json.get("total_trades")
    try:
        total_i = int(total) if total is not None else None
    except Exception:
        total_i = None

    if total_i is not None:
        nums.append({"k": "学習データ数（trades）", "v": total_i, "unit": "件"})

    # 例: あるなら拾う（無くてもOK）
    auc = pick("auc", "AUC")
    if auc is not None:
        nums.append({"k": "モデル評価（AUC）", "v": auc, "unit": ""})

    logloss = pick("logloss", "log_loss")
    if logloss is not None:
        nums.append({"k": "モデル評価（logloss）", "v": logloss, "unit": ""})

    acc = pick("accuracy", "acc")
    if acc is not None:
        nums.append({"k": "モデル評価（accuracy）", "v": acc * 100.0 if acc <= 1.0 else acc, "unit": "%"})

    # 何を当ててるか（あれば）
    target = pick_str("target", "label", "task", "objective")
    if target:
        nums.append({"k": "学習ターゲット", "v": target, "unit": ""})

    trained_at = pick_str("trained_at", "trainedAt", "updated_at", "model_updated_at")
    if trained_at:
        nums.append({"k": "最終学習時刻", "v": trained_at, "unit": ""})

    ver = pick_str("model_version", "version", "commit")
    if ver:
        nums.append({"k": "モデル版", "v": ver, "unit": ""})

    return nums[:8]


def _ml_beginner_text() -> List[str]:
    """
    画面に出す “初心者向け説明” を固定文で用意（ここが一番大事）。
    """
    return [
        "ML（機械学習）が学習しているのは「この形（Entry/TP/SL）だと、TPが先に当たりやすい？それともSLが先？」という“順番の確率”。",
        "出力は p_tp_first（TPがSLより先になる確率 0〜1）。低いほど危険なので、エントリーを押し目寄せ＆RR（利幅/損幅）を上げて“勝てる形”に寄せる。",
        "重要：MLは「あなたの⭐️（BehaviorStats）」を直接いじらない。⭐️は過去成績の統計（勝率×平均R×試行数）で決まる。",
    ]


def _ml_where_used_text() -> List[Dict[str, str]]:
    """
    “どこに反映してる？” を画面に出すための固定リスト。
    """
    return [
        {"k": "Entry/TP/SL の補正", "v": "aiapp/services/entry_service.py：p_tp_first が低いほど、飛びつきを抑えて押し目寄せ＋RRを引き上げ"},
        {"k": "EV_true（本命期待値）", "v": "aiapp/services/sizing_service.py：EV_true = pTP*RR_net − pSL*1（pSLは 1−pTP−pNone の推定も可）"},
        {"k": "⭐️（評価）", "v": "BehaviorStats（統計）：VirtualTrade.result_r_pro の実績から勝率/平均R/試行数→score→⭐️"},
    ]


# =========================
# view
# =========================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ PRO仕様の行動ダッシュボード（テロップ + entry_reason別）
    - simulate/*.jsonl を集計
    - behavior/model/latest_behavior_model_u{user}.json を読む
    - behavior/latest_behavior_side.jsonl を読む（まずはこれで entry_reason 別可視化）
    - behavior/ticker/latest_ticker_u{user}.json を読む
    """
    user = request.user
    today = timezone.localdate()
    today_label = today.strftime("%Y-%m-%d")

    media_root = Path("media")
    sim_dir = media_root / "aiapp" / "simulate"
    beh_dir = media_root / "aiapp" / "behavior"

    model_path = beh_dir / "model" / f"latest_behavior_model_u{user.id}.json"
    side_path = beh_dir / "latest_behavior_side.jsonl"
    ticker_path = beh_dir / "ticker" / f"latest_ticker_u{user.id}.json"

    sim_sum = _summarize_simulate_dir(sim_dir)

    model_json = _read_json(model_path) or {}
    has_model = bool(model_json)

    win_rate = _safe_float(model_json.get("win_rate"))
    avg_pl = _safe_float(model_json.get("avg_pl"))
    avg_r = _safe_float(model_json.get("avg_r"))
    total_trades = int(model_json.get("total_trades") or 0)

    wl_total = int(sim_sum.get("wl") or 0)
    sim_total = int(sim_sum.get("total_qty") or 0)
    eval_done = int(sim_sum.get("eval_done") or 0)
    labels = sim_sum.get("labels") or {}

    has_data = (wl_total >= 1) or (eval_done >= 1)

    side_rows = _read_jsonl(side_path)

    wl_labels: List[str] = []
    for r in side_rows:
        lab = str(r.get("eval_label") or "").strip().lower()
        if lab in ("win", "lose", "flat"):
            wl_labels.append(lab)

    seq = _make_sequence(wl_labels)
    streak_label, streak_len = _streak_from_labels(wl_labels)
    streak_text = "none" if streak_len == 0 else f"{streak_label.upper()} x{streak_len}"

    ticker = _load_ticker(ticker_path)

    # entry_reason 別（sideを元にまず成立させる）
    entry_reason_stats = _build_entry_reason_stats_from_side(side_rows)

    # ★初心者向け：MLの説明と数値を抽出（model_jsonが薄くても落ちない）
    ml_beginner = _ml_beginner_text()
    ml_numbers = _extract_ml_numbers(model_json if isinstance(model_json, dict) else {})
    ml_top_features = _extract_feature_importance(model_json if isinstance(model_json, dict) else {})
    ml_where_used = _ml_where_used_text()

    if not has_data:
        return render(
            request,
            "aiapp/behavior_dashboard.html",
            {
                "has_data": False,
                "today_label": today_label,
                "sim_files": sim_sum.get("files", 0),
                "understanding_label": _understanding_label(0),
                "model": {"has_model": has_model, "total_trades": total_trades},
                "ticker_date": ticker.get("date", ""),
                "ticker_lines": ticker.get("lines", []),
                "entry_reason_stats": entry_reason_stats,

                # beginner/ml
                "ml_beginner": ml_beginner,
                "ml_numbers": ml_numbers,
                "ml_top_features": ml_top_features,
                "ml_where_used": ml_where_used,
            },
        )

    understanding_label = _understanding_label(wl_total)

    hypotheses = _make_hypotheses(
        wl_total=wl_total,
        win_rate=win_rate,
        avg_r=avg_r,
        avg_pl=avg_pl,
        labels=labels,
        streak_label=streak_label,
        streak_len=streak_len,
    )
    notes = _make_notes(wl_total=wl_total, sim_total=sim_total, eval_done=eval_done, labels=labels)
    bias_map = _make_bias_map(win_rate=win_rate, avg_r=avg_r, labels=labels)
    wanted = _make_wanted(
        wl_total=wl_total,
        labels=labels,
        avg_r=avg_r,
        streak_label=streak_label,
        streak_len=streak_len,
    )
    recent_trades = _extract_recent_trades(side_rows, limit=8)

    ctx = {
        "has_data": True,
        "today_label": today_label,
        "sim_files": int(sim_sum.get("files") or 0),
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
        "sequence": seq,
        "streak_label": streak_text,
        "recent_trades": recent_trades,
        "model": {
            "has_model": has_model,
            "total_trades": total_trades,
        },
        "ticker_date": ticker.get("date", ""),
        "ticker_lines": ticker.get("lines", []),
        "entry_reason_stats": entry_reason_stats,

        # beginner/ml
        "ml_beginner": ml_beginner,
        "ml_numbers": ml_numbers,
        "ml_top_features": ml_top_features,
        "ml_where_used": ml_where_used,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)