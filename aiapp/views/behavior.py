# aiapp/views/behavior.py
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime

from django.conf import settings
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


def _pick_any_float(r: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for k in keys:
        if k in r:
            v = _safe_float(r.get(k))
            if v is not None:
                return v
    return None


def _extract_recent_trades(side_rows: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """
    評価済み（side）を表示する部分。
    ※ ここは「最近の評価（抜粋）」用なので、MLが入ってない古い行でもOK。
    """
    out: List[Dict[str, Any]] = []
    for r in side_rows[-limit:]:
        code = str(r.get("code") or "")
        label = str(r.get("eval_label") or "").strip().lower()
        pl = _safe_float(r.get("eval_pl")) or 0.0
        rv = _safe_float(r.get("eval_r"))
        mode = str(r.get("mode") or "").strip().lower()
        broker = str(r.get("broker") or "pro").strip().lower()
        ts = str(r.get("ts") or r.get("trade_date") or "")

        # --- ML 推論（拾えるだけ拾う） ---
        p_win = _pick_any_float(r, ["p_win", "ml_pwin", "ml_p_win"])
        p_tp_first = _pick_any_float(r, ["p_tp_first", "ml_p_tp_first", "ml_tp_first"])
        ev_pred = _pick_any_float(r, ["ev_pred", "ml_ev_pred", "ev_ml", "pred_ev"])
        ev_true = _pick_any_float(r, ["ev_true", "ml_ev_true"])

        # --- Shape（Entry/TP/SLの係数など。無ければNone） ---
        # ここは side_rows 側なので「shape_*」優先にしておく
        shape_entry_k = _pick_any_float(r, ["shape_entry_k", "entry_k"])
        shape_rr_target = _pick_any_float(r, ["shape_rr_target", "rr_target"])
        shape_tp_k = _pick_any_float(r, ["shape_tp_k", "tp_k"])
        shape_sl_k = _pick_any_float(r, ["shape_sl_k", "sl_k"])

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

                # ML numbers
                "p_win": p_win,
                "p_tp_first": p_tp_first,
                "ev_pred": ev_pred,
                "ev_true": ev_true,

                # Shape numbers
                "shape_entry_k": shape_entry_k,
                "shape_rr_target": shape_rr_target,
                "shape_tp_k": shape_tp_k,
                "shape_sl_k": shape_sl_k,
            }
        )

    # 新しいものを上に
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


def _load_ml_latest_meta() -> Dict[str, Any]:
    """
    media/aiapp/ml/models/latest/meta.json を読み込む
    無ければ空 dict。
    """
    try:
        base = Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "models" / "latest"
        p = base / "meta.json"
        j = _read_json(p) or {}
        m = j.get("metrics")
        if not isinstance(m, dict):
            j["metrics"] = {}
        return j
    except Exception:
        return {"metrics": {}}


def _get_metrics_block(metrics: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """
    metrics の中のブロック名が揺れても拾う。
    例: tp_first / p_tp_first / tp など
    """
    for k in keys:
        v = metrics.get(k)
        if isinstance(v, dict) and v:
            return v
    return {}


# =========================
# ★ 信号機 + 初心者向け説明（辞書化）
# =========================

def _sig(level: str) -> Dict[str, str]:
    """
    level: "green" | "yellow" | "red"
    """
    if level == "green":
        return {"level": "green", "label": "GO", "desc": "だいたい信じていい（効いてる）"}
    if level == "yellow":
        return {"level": "yellow", "label": "CAUTION", "desc": "効いてるが過信は禁物（条件次第）"}
    return {"level": "red", "label": "STOP", "desc": "まだ荒い/運寄り（単体で判断しない）"}


def _rate_auc(v: Optional[float]) -> Dict[str, Any]:
    if v is None:
        return {"signal": _sig("red"), "comment": "未計測"}
    if v < 0.55:
        return {"signal": _sig("red"), "comment": "ほぼ運に近い"}
    if v < 0.60:
        return {"signal": _sig("yellow"), "comment": "ちょいマシ（運よりはマシ）"}
    return {"signal": _sig("green"), "comment": "効いてる（順番付けが上手い）"}


def _rate_acc(v: Optional[float]) -> Dict[str, Any]:
    if v is None:
        return {"signal": _sig("red"), "comment": "未計測"}
    if v < 0.55:
        return {"signal": _sig("red"), "comment": "まだ弱い"}
    if v < 0.60:
        return {"signal": _sig("yellow"), "comment": "そこそこ（過信は禁物）"}
    return {"signal": _sig("green"), "comment": "良い（判断に使える）"}


def _rate_logloss(v: Optional[float]) -> Dict[str, Any]:
    # 0.693 付近は“運”ラインとして扱う
    if v is None:
        return {"signal": _sig("red"), "comment": "未計測"}
    if v <= 0.60:
        return {"signal": _sig("green"), "comment": "確率がかなり安定"}
    if v <= 0.69:
        return {"signal": _sig("yellow"), "comment": "まあまあ（揺れはある）"}
    return {"signal": _sig("red"), "comment": "まだ荒い（確率がブレやすい）"}


def _rate_mae_rmse(v: Optional[float]) -> Dict[str, Any]:
    # EV誤差を“R感覚”で初心者向けに割り切り分類
    if v is None:
        return {"signal": _sig("red"), "comment": "未計測"}
    if v <= 0.50:
        return {"signal": _sig("green"), "comment": "ズレ小さめ"}
    if v <= 1.00:
        return {"signal": _sig("yellow"), "comment": "ズレあり（参考程度）"}
    return {"signal": _sig("red"), "comment": "ズレ大きい（信じすぎ注意）"}


def _rate_valid_rows(v: Optional[int]) -> Dict[str, Any]:
    if v is None:
        return {"signal": _sig("red"), "comment": "未計測"}
    if v < 50:
        return {"signal": _sig("red"), "comment": "少ない（数字が揺れる）"}
    if v < 150:
        return {"signal": _sig("yellow"), "comment": "育成中（揺れは残る）"}
    return {"signal": _sig("green"), "comment": "安定し始め（信頼度UP）"}


def _ml_explain_pack(ml_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    テンプレでそのまま出せる“説明つき”辞書を返す。
    """
    # 値
    pwin_auc = _safe_float(ml_metrics.get("pwin_auc"))
    pwin_logloss = _safe_float(ml_metrics.get("pwin_logloss"))
    ev_mae = _safe_float(ml_metrics.get("ev_mae"))
    ev_rmse = _safe_float(ml_metrics.get("ev_rmse"))
    tp_acc = _safe_float(ml_metrics.get("tp_acc"))
    tp_logloss = _safe_float(ml_metrics.get("tp_logloss"))
    rows = int(ml_metrics.get("rows") or 0)
    valid_rows = int(ml_metrics.get("valid_rows") or 0)
    train_rows = int(ml_metrics.get("train_rows") or 0)

    out: Dict[str, Any] = {}

    # p_win
    out["p_win_auc"] = {
        "key": "p_win_auc",
        "name": "p_win（勝つ確率）AUC",
        "value": pwin_auc,
        "direction": "up",
        "signal": _rate_auc(pwin_auc)["signal"],
        "comment": _rate_auc(pwin_auc)["comment"],
        "bullets": [
            "AUC：勝つ/負けるの“順番付け”がどれくらい上手いか（0.5=運、1.0=神）",
            "0.60〜で効き始め。0.55〜0.60は「ちょい効く」ゾーン。",
        ],
    }
    out["p_win_logloss"] = {
        "key": "p_win_logloss",
        "name": "p_win（勝つ確率）logloss",
        "value": pwin_logloss,
        "direction": "down",
        "signal": _rate_logloss(pwin_logloss)["signal"],
        "comment": _rate_logloss(pwin_logloss)["comment"],
        "bullets": [
            "logloss：確率の出し方がどれくらい正確か（小さいほど良い）",
            "0.69付近は“運”ライン。0.60以下は安定し始め。",
        ],
    }

    # EV
    out["ev_mae"] = {
        "key": "ev_mae",
        "name": "EV（期待値推定）MAE",
        "value": ev_mae,
        "direction": "down",
        "signal": _rate_mae_rmse(ev_mae)["signal"],
        "comment": _rate_mae_rmse(ev_mae)["comment"],
        "bullets": [
            "MAE：予測の平均ズレ（小さいほど良い）",
            "0.5以下=ズレ小、1.0超=ズレ大（信じすぎ注意）",
        ],
    }
    out["ev_rmse"] = {
        "key": "ev_rmse",
        "name": "EV（期待値推定）RMSE",
        "value": ev_rmse,
        "direction": "down",
        "signal": _rate_mae_rmse(ev_rmse)["signal"],
        "comment": _rate_mae_rmse(ev_rmse)["comment"],
        "bullets": [
            "RMSE：大きく外した時により厳しく罰するズレ（小さいほど良い）",
            "MAEより悪化してると『たまに大外し』があるサイン。",
        ],
    }

    # tp_first
    out["tp_acc"] = {
        "key": "tp_acc",
        "name": "tp_first（TP先/SL先）ACC",
        "value": tp_acc,
        "direction": "up",
        "signal": _rate_acc(tp_acc)["signal"],
        "comment": _rate_acc(tp_acc)["comment"],
        "bullets": [
            "ACC：当たった割合（大きいほど良い）",
            "0.55〜0.60は“ちょい効く”。0.60以上で強くなってくる。",
        ],
    }
    out["tp_logloss"] = {
        "key": "tp_logloss",
        "name": "tp_first（TP先/SL先）logloss",
        "value": tp_logloss,
        "direction": "down",
        "signal": _rate_logloss(tp_logloss)["signal"],
        "comment": _rate_logloss(tp_logloss)["comment"],
        "bullets": [
            "logloss：確率の荒さ（小さいほど良い）",
            "ACCがそこそこでもloglossが悪いと『確率の自信がブレる』。",
        ],
    }

    # data volume
    out["rows"] = {
        "key": "rows",
        "name": "学習データ量（total rows）",
        "value": float(rows) if rows else None,
        "direction": "up",
        "signal": _rate_valid_rows(rows)["signal"],
        "comment": _rate_valid_rows(rows)["comment"],
        "bullets": [
            "データ量が少ないと、AUC/ACC/誤差が日ごとに揺れやすい。",
            "まずはvalidを増やすと“数字が安定”し始める。",
        ],
    }
    out["valid_rows"] = {
        "key": "valid_rows",
        "name": "検証データ量（valid）",
        "value": float(valid_rows) if valid_rows else None,
        "direction": "up",
        "signal": _rate_valid_rows(valid_rows)["signal"],
        "comment": _rate_valid_rows(valid_rows)["comment"],
        "bullets": [
            "validが少ないと『偶然当たった/外れた』の影響を強く受ける。",
            "目安：<50は赤、50〜150は黄、150以上で緑に寄る。",
        ],
    }
    out["train_rows"] = {
        "key": "train_rows",
        "name": "学習データ量（train）",
        "value": float(train_rows) if train_rows else None,
        "direction": "up",
        "signal": _rate_valid_rows(train_rows)["signal"],
        "comment": _rate_valid_rows(train_rows)["comment"],
        "bullets": [
            "trainが増えると学習の材料は増えるが、評価はvalidが重要。",
            "trainだけ多くても、validが少ないと信号機は赤〜黄に寄る。",
        ],
    }

    return out


def _ml_latest_explain_pack(ml_latest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    「最新のML推論（実数値）」の初心者向け説明（信号機付き）。
    ここは “精度” ではなく “今回の出力の意味” を整理する。
    """
    if not ml_latest:
        return {}

    p_win = _safe_float(ml_latest.get("p_win"))
    p_tp_first = _safe_float(ml_latest.get("p_tp_first"))
    ev_pred = _safe_float(ml_latest.get("ev_pred"))
    ev_true = _safe_float(ml_latest.get("ev_true"))

    # ここは“当たり外れ”ではなく「読み方」の信号機にする（過信防止）
    # p_win: 0.55以上なら緑寄り、0.45〜0.55黄、0.45未満赤
    def rate_prob(v: Optional[float]) -> Dict[str, Any]:
        if v is None:
            return {"signal": _sig("red"), "comment": "未出力"}
        if v >= 0.55:
            return {"signal": _sig("green"), "comment": "勝ち寄り（ただし過信禁止）"}
        if v >= 0.45:
            return {"signal": _sig("yellow"), "comment": "五分〜やや不利（形の補正が重要）"}
        return {"signal": _sig("red"), "comment": "不利寄り（形を厳しめにする）"}

    # EVは符号が大事：プラスなら緑寄り、ゼロ付近黄、マイナス赤（ただし単体で決めない）
    def rate_ev(v: Optional[float]) -> Dict[str, Any]:
        if v is None:
            return {"signal": _sig("red"), "comment": "未出力"}
        if v > 0.10:
            return {"signal": _sig("green"), "comment": "プラス寄り（期待値あり）"}
        if v >= 0.0:
            return {"signal": _sig("yellow"), "comment": "微妙（形補正後が勝負）"}
        return {"signal": _sig("red"), "comment": "マイナス寄り（見送り候補）"}

    return {
        "p_win": {
            "name": "p_win（勝つ確率）",
            "value": p_win,
            "signal": rate_prob(p_win)["signal"],
            "comment": rate_prob(p_win)["comment"],
            "bullets": [
                "0〜1の確率。大きいほど“勝ち寄り”。",
                "ただしこれ単体で売買を決めない（データ量/相場/形補正が絡む）。",
            ],
        },
        "p_tp_first": {
            "name": "p_tp_first（TP先になりやすさ）",
            "value": p_tp_first,
            "signal": rate_prob(p_tp_first)["signal"],
            "comment": rate_prob(p_tp_first)["comment"],
            "bullets": [
                "TP先（利確先）になりやすい確率。大きいほど“利確に届きやすい”。",
                "低いと『飛びつき抑制』『RR引き上げ』など、形が厳しくなる。",
            ],
        },
        "ev_pred": {
            "name": "EV_pred（予測EV）",
            "value": ev_pred,
            "signal": rate_ev(ev_pred)["signal"],
            "comment": rate_ev(ev_pred)["comment"],
            "bullets": [
                "モデルが予測した期待値（プラスが望ましい）。",
                "これは“予測段階”。次のEV_trueが“形補正後”。",
            ],
        },
        "ev_true": {
            "name": "EV_true（形に反映後）",
            "value": ev_true,
            "signal": rate_ev(ev_true)["signal"],
            "comment": rate_ev(ev_true)["comment"],
            "bullets": [
                "形（Entry/TP/SL補正）を入れた後の期待値。",
                "EV_trueが0付近なら『形の補正で無理をしない』判断が働いている可能性。",
            ],
        },
    }


def _ml_metrics_view(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    テンプレ表示用に要約して渡す。
    ※ tp_first が “-” になる問題は、meta.json のキー揺れを吸収して解決する。
    """
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}

    pwin = _get_metrics_block(metrics, ["p_win", "pwin", "win"])
    ev = _get_metrics_block(metrics, ["ev", "expected_value"])
    tp = _get_metrics_block(metrics, ["tp_first", "p_tp_first", "tp"])
    hold = _get_metrics_block(metrics, ["hold_days_pred", "hold_days", "hold"])

    # accuracy のキーも揺れがちなので吸収
    tp_acc = _safe_float(tp.get("accuracy"))
    if tp_acc is None:
        tp_acc = _safe_float(tp.get("acc"))

    out = {
        "created_at": str(meta.get("created_at") or ""),
        "rows": int(meta.get("rows") or 0),
        "train_rows": int(meta.get("train_rows") or 0),
        "valid_rows": int(meta.get("valid_rows") or 0),
        "best_iteration": meta.get("best_iteration") if isinstance(meta.get("best_iteration"), dict) else {},

        "pwin_auc": _safe_float(pwin.get("auc")),
        "pwin_logloss": _safe_float(pwin.get("logloss")),

        "ev_rmse": _safe_float(ev.get("rmse")),
        "ev_mae": _safe_float(ev.get("mae")),

        "tp_acc": tp_acc,
        "tp_logloss": _safe_float(tp.get("logloss")),

        "hold_mae": _safe_float(hold.get("mae")),

        # 表示分岐
        "has_metrics": bool(metrics) and (int(meta.get("valid_rows") or 0) > 0),
        "has_tp_first": (tp_acc is not None) or (_safe_float(tp.get("logloss")) is not None),
        "has_hold_days": (_safe_float(hold.get("mae")) is not None),
    }
    return out


def _parse_ts_any(s: str) -> Optional[datetime]:
    """
    ts の ISO文字列をできる範囲で datetime にする（失敗しても落とさない）
    """
    if not s:
        return None
    try:
        # "2025-12-26T17:56:40.770568+09:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _select_latest_ml_inference(
    dataset_rows: List[Dict[str, Any]],
    user_id: int,
) -> Optional[Dict[str, Any]]:
    """
    latest_behavior.jsonl（= raw simulate ログ）から
    「ML実数値が入っている最新の1件」を選ぶ。

    ✅ “確実に最新を拾う” ために末尾（最新）から走査する。
    優先:
      1) ml_ok == True かつ ML数値が1つでも入ってる行（最新から最初に見つかったもの）
      2) ML数値が1つでも入ってる行（最新から最初に見つかったもの）
      3) それも無ければ None

    ★重要：user_id / ml_ok が「直下に無い」ログでも拾えるようにする
      - raw 直下
      - sim_order
      - replay.sim_order
    をマージして判定する。
    """
    if not dataset_rows:
        return None

    def merged_view(x: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(x or {})
        so = x.get("sim_order")
        if isinstance(so, dict) and so:
            out.update(so)
        rp = x.get("replay")
        if isinstance(rp, dict) and rp:
            so2 = rp.get("sim_order")
            if isinstance(so2, dict) and so2:
                out.update(so2)
        return out

    def user_match(x: Dict[str, Any]) -> bool:
        m = merged_view(x)
        uid = m.get("user_id", None)
        if uid is None:
            # user_id が無い行は “現ユーザー扱い” で拾う（今回のshape消失の根本対策）
            return True
        try:
            return int(uid) == int(user_id)
        except Exception:
            return True

    def has_any_ml(x: Dict[str, Any]) -> bool:
        m = merged_view(x)
        return (
            (m.get("ml_ok") is True)
            or (_safe_float(m.get("p_win")) is not None)
            or (_safe_float(m.get("ev_pred")) is not None)
            or (_safe_float(m.get("p_tp_first")) is not None)
        )

    # 1) ml_ok True を最新から
    for r in reversed(dataset_rows):
        if not user_match(r):
            continue
        m = merged_view(r)
        if m.get("ml_ok") is True and has_any_ml(r):
            return r

    # 2) any ml fields を最新から
    for r in reversed(dataset_rows):
        if not user_match(r):
            continue
        if has_any_ml(r):
            return r

    return None


def _merge_sim_order_sources(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    shape/係数がどこに入ってても拾えるように、候補をマージする。
    優先順:
      1) raw 直下
      2) raw["sim_order"]
      3) raw["replay"]["sim_order"]
    ※ 後勝ち（後のdictで上書き）だと優先が逆になるので、手動で順序を作る。
    """
    merged: Dict[str, Any] = {}
    if isinstance(raw, dict):
        merged.update(raw)

        so = raw.get("sim_order")
        if isinstance(so, dict) and so:
            # sim_order の方が “本命”になりがちなので上書きOK
            merged.update(so)

        rp = raw.get("replay")
        if isinstance(rp, dict) and rp:
            so2 = rp.get("sim_order")
            if isinstance(so2, dict) and so2:
                merged.update(so2)

    return merged


def _ml_latest_view_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    テンプレ（ml_latest.*）に合わせてキーを整形して返す。
    - ML数値は直下/ネストをまとめて拾う
    - Shapeは shape_* を最優先で拾う（無ければ entry_k 等）
    """
    merged = _merge_sim_order_sources(raw or {})

    # ML numbers（キー揺れも吸収）
    p_win = _pick_any_float(merged, ["p_win", "ml_pwin", "ml_p_win"])
    p_tp_first = _pick_any_float(merged, ["p_tp_first", "ml_p_tp_first", "ml_tp_first"])
    ev_pred = _pick_any_float(merged, ["ev_pred", "ml_ev_pred", "ev_ml", "pred_ev"])
    ev_true = _pick_any_float(merged, ["ev_true", "ml_ev_true"])

    # Shape（ここが白枠の本体）
    shape_entry_k = _pick_any_float(merged, ["shape_entry_k", "entry_k"])
    shape_rr_target = _pick_any_float(merged, ["shape_rr_target", "rr_target"])
    shape_tp_k = _pick_any_float(merged, ["shape_tp_k", "tp_k"])
    shape_sl_k = _pick_any_float(merged, ["shape_sl_k", "sl_k"])

    return {
        "code": str(merged.get("code") or raw.get("code") or ""),

        "p_win": p_win,
        "p_tp_first": p_tp_first,
        "ev_pred": ev_pred,
        "ev_true": ev_true,

        "shape_entry_k": shape_entry_k,
        "shape_rr_target": shape_rr_target,
        "shape_tp_k": shape_tp_k,
        "shape_sl_k": shape_sl_k,
    }


# =========================
# view
# =========================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ PRO仕様の行動ダッシュボード（テロップ + entry_reason別 + MLメトリクス）
    - simulate/*.jsonl を集計
    - behavior/model/latest_behavior_model_u{user}.json を読む
    - behavior/latest_behavior_side.jsonl を読む
    - behavior/latest_behavior.jsonl を読む（← 追加：最新ML推論の実数値用）
    - behavior/ticker/latest_ticker_u{user}.json を読む
    - ml/models/latest/meta.json を読む（AUC/RMSEなど）
    """
    user = request.user
    today = timezone.localdate()
    today_label = today.strftime("%Y-%m-%d")

    media_root = Path(settings.MEDIA_ROOT)

    sim_dir = media_root / "aiapp" / "simulate"
    beh_dir = media_root / "aiapp" / "behavior"

    model_path = beh_dir / "model" / f"latest_behavior_model_u{user.id}.json"
    side_path = beh_dir / "latest_behavior_side.jsonl"
    dataset_path = beh_dir / "latest_behavior.jsonl"
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

    # entry_reason 別
    entry_reason_stats = _build_entry_reason_stats_from_side(side_rows)

    # ★ ML metrics（AUC/RMSEなど）
    ml_meta = _load_ml_latest_meta()
    ml_metrics = _ml_metrics_view(ml_meta)
    ml_explain = _ml_explain_pack(ml_metrics)

    # ★ 最新ML推論（実数値）は raw dataset から拾う
    dataset_rows = _read_jsonl(dataset_path)
    raw_latest = _select_latest_ml_inference(dataset_rows, user_id=user.id)
    ml_latest = _ml_latest_view_from_raw(raw_latest) if raw_latest else None
    ml_latest_explain = _ml_latest_explain_pack(ml_latest)

    if not has_data:
        return render(
            request,
            "aiapp/behavior_dashboard.html",
            {
                "has_data": False,
                "today_label": today_label,
                "sim_files": sim_sum.get("files", 0),
                "understanding_label": _understanding_label(0),
                "model": {"has_model": has_model},
                "ticker_date": ticker.get("date", ""),
                "ticker_lines": ticker.get("lines", []),
                "entry_reason_stats": entry_reason_stats,
                "ml": ml_metrics,
                "ml_explain": ml_explain,
                "ml_latest": ml_latest,
                "ml_latest_explain": ml_latest_explain,
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

    # 最近の評価（side）
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
        "ml": ml_metrics,
        "ml_explain": ml_explain,
        "ml_latest": ml_latest,
        "ml_latest_explain": ml_latest_explain,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)