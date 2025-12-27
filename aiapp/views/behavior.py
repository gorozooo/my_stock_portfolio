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


def _as_raw_str(v: Any) -> str:
    """
    ✅ 表示用 value は「丸めない」方針
    - None は "-"
    - 数値は str() でそのまま（丸めない）
    """
    if v is None:
        return "-"
    # bool は数値と混ざるので明示
    if isinstance(v, bool):
        return "true" if v else "false"
    try:
        return str(v)
    except Exception:
        return "-"


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


def _ml_metrics_view(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    テンプレ表示用に要約して渡す。
    """
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}

    pwin = _get_metrics_block(metrics, ["p_win", "pwin", "win"])
    ev = _get_metrics_block(metrics, ["ev", "expected_value"])
    tp = _get_metrics_block(metrics, ["tp_first", "p_tp_first", "tp"])

    # accuracy のキーも揺れがちなので吸収
    tp_acc = _safe_float(tp.get("accuracy"))
    if tp_acc is None:
        tp_acc = _safe_float(tp.get("acc"))

    out = {
        "created_at": str(meta.get("created_at") or ""),
        "rows": int(meta.get("rows") or 0),
        "train_rows": int(meta.get("train_rows") or 0),
        "valid_rows": int(meta.get("valid_rows") or 0),

        "pwin_auc": _safe_float(pwin.get("auc")),
        "pwin_logloss": _safe_float(pwin.get("logloss")),

        "ev_rmse": _safe_float(ev.get("rmse")),
        "ev_mae": _safe_float(ev.get("mae")),

        "tp_acc": tp_acc,
        "tp_logloss": _safe_float(tp.get("logloss")),

        # 表示分岐
        "has_metrics": bool(metrics) and (int(meta.get("valid_rows") or 0) > 0),
    }
    return out


# =========================
# ✅ 信号機UI用：説明/判定
# =========================

def _signal_for_metric(name: str, v: Optional[float]) -> str:
    """
    return: "red" | "yellow" | "green" | "na"
    初心者用の“ざっくり”基準（あとで調整しやすい）
    """
    if v is None:
        return "na"

    # AUC: 0.5=運 / 0.6〜効き始め / 0.7〜強い
    if name == "auc":
        if v < 0.55:
            return "red"
        if v < 0.65:
            return "yellow"
        return "green"

    # logloss: 小さいほど良い（分類）
    # 目安: ~0.55 good / 0.55-0.70 ok / >0.70 rough
    if name == "logloss":
        if v <= 0.60:
            return "green"
        if v <= 0.70:
            return "yellow"
        return "red"

    # ACC: 0.5=運 / 0.55〜効き / 0.6〜強い
    if name == "acc":
        if v < 0.53:
            return "red"
        if v < 0.60:
            return "yellow"
        return "green"

    # MAE/RMSE: 小さいほど良い（ただしスケール依存）
    # ここは “固定の絶対評価” をしないで「相対」っぽく扱う。
    # 初期は常に yellow を返して、学習の進み具合を“比較”で見る前提。
    if name in ("mae", "rmse"):
        return "yellow"

    return "yellow"


def _metric_card(
    title: str,
    value_raw: Any,
    direction: str,
    explain_lines: List[str],
    signal_key: str,
    units_hint: str = "",
) -> Dict[str, Any]:
    """
    ✅ テンプレに渡す 1枚のメトリクスカード
    - value: 丸めない（str(v)）
    - direction: "up" or "down"
    - signal: "red/yellow/green/na"
    - explain_lines: 初心者向け説明（箇条書き）
    """
    v = _safe_float(value_raw)
    sig = _signal_for_metric(signal_key, v)
    return {
        "title": title,
        "value": _as_raw_str(value_raw),   # ✅ 丸めない
        "direction": direction,            # "up" or "down"
        "signal": sig,                     # red/yellow/green/na
        "explain_lines": explain_lines[:6],
        "units_hint": units_hint,
    }


def _build_ml_metric_cards(ml: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ML学習精度（valid）を「信号機＋初心者説明」に変換して返す
    """
    if not ml or not ml.get("has_metrics"):
        return []

    cards: List[Dict[str, Any]] = []

    # p_win: AUC / logloss
    cards.append(
        _metric_card(
            title="p_win（勝つ確率）AUC",
            value_raw=ml.get("pwin_auc"),
            direction="up",
            signal_key="auc",
            explain_lines=[
                "AUC：勝つ/負けるの“順番付け”がどれくらい上手いか（0.5=運、1.0=神）",
                "0.55前後：運よりはマシ / 0.60〜：効き始め / 0.70〜：強い",
                "これは“当たる確率そのもの”ではなく“並べ方の上手さ”",
            ],
        )
    )
    cards.append(
        _metric_card(
            title="p_win（勝つ確率）logloss",
            value_raw=ml.get("pwin_logloss"),
            direction="down",
            signal_key="logloss",
            explain_lines=[
                "logloss：出した“確率”がどれくらい正確か（小さいほど良い）",
                "0に近いほど『確率が当たりやすい』＝自信の出し方が上手い",
                "0.70超：まだ荒い（確率がブレやすい）",
            ],
        )
    )

    # EV: MAE / RMSE（スケール依存）
    cards.append(
        _metric_card(
            title="EV（期待値）MAE",
            value_raw=ml.get("ev_mae"),
            direction="down",
            signal_key="mae",
            units_hint="（単位はEV定義に依存）",
            explain_lines=[
                "MAE：予測EVと実際EVの“平均ズレ量”（小さいほど良い）",
                "0.0なら“ズレが無い”が、現実はそうならない",
                "ここは絶対評価より『昨日より良いか』で見るのが正解",
            ],
        )
    )
    cards.append(
        _metric_card(
            title="EV（期待値）RMSE",
            value_raw=ml.get("ev_rmse"),
            direction="down",
            signal_key="rmse",
            units_hint="（単位はEV定義に依存）",
            explain_lines=[
                "RMSE：大きく外した時に、より強くペナルティを与えるズレ指標（小さいほど良い）",
                "MAEより“やらかし”に敏感（外れが多いとRMSEが上がる）",
                "MAEとRMSEの両方が下がっていくと改善が本物",
            ],
        )
    )

    # tp_first: ACC / logloss
    cards.append(
        _metric_card(
            title="tp_first（TP先/SL先）ACC",
            value_raw=ml.get("tp_acc"),
            direction="up",
            signal_key="acc",
            explain_lines=[
                "ACC：TP先かSL先かを当てる“的中率”（0.5=運）",
                "0.55〜：効き始め / 0.60〜：強い",
                "これは“利確か損切りか”の方向感の学習",
            ],
        )
    )
    cards.append(
        _metric_card(
            title="tp_first（TP先/SL先）logloss",
            value_raw=ml.get("tp_logloss"),
            direction="down",
            signal_key="logloss",
            explain_lines=[
                "logloss：tp_first の“確率”の出し方がどれくらい正確か（小さいほど良い）",
                "ACCが同じでも、loglossが低い方が“確率の自信”が上手い",
            ],
        )
    )

    # 学習データ量
    cards.append(
        {
            "title": "学習データ量（valid）",
            "value": _as_raw_str(ml.get("rows")),
            "direction": "up",
            "signal": "yellow",
            "explain_lines": [
                "学習が進むほど“型”が安定する（ただし質が最優先）",
                "train/valid の内訳も一緒に見るのが安全",
            ],
            "units_hint": f"train {ml.get('train_rows')} / valid {ml.get('valid_rows')} / created {ml.get('created_at')}",
        }
    )

    return cards


def _parse_ts_any(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
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

    def has_any_ml(x: Dict[str, Any]) -> bool:
        m = merged_view(x)
        return (
            x.get("ml_ok") is True
            or _safe_float(m.get("p_win")) is not None
            or _safe_float(m.get("ev_pred")) is not None
            or _safe_float(m.get("p_tp_first")) is not None
        )

    for r in reversed(dataset_rows):
        if int(r.get("user_id") or 0) != int(user_id):
            continue
        if r.get("ml_ok") is True and has_any_ml(r):
            return r

    for r in reversed(dataset_rows):
        if int(r.get("user_id") or 0) != int(user_id):
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
    """
    merged: Dict[str, Any] = {}
    if isinstance(raw, dict):
        merged.update(raw)

        so = raw.get("sim_order")
        if isinstance(so, dict) and so:
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

    # Shape（白枠の本体）
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


def _signal_for_prob(v: Optional[float]) -> str:
    """
    推論値（確率/EV）に対する初心者用の目安
    - ここは “参考” なので雑に
    """
    if v is None:
        return "na"
    # 0.5を超えるほど良い、ただし過信禁止
    if v < 0.45:
        return "red"
    if v < 0.55:
        return "yellow"
    return "green"


def _signal_for_ev(v: Optional[float]) -> str:
    if v is None:
        return "na"
    if v <= 0:
        return "red"
    if v < 0.15:
        return "yellow"
    return "green"


def _build_ml_latest_cards(ml_latest: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    最新のML推論（実数値）を、信号機＋説明つきカードに変換
    ✅ value は丸めない
    """
    if not ml_latest:
        return []

    p_win = _safe_float(ml_latest.get("p_win"))
    p_tp_first = _safe_float(ml_latest.get("p_tp_first"))
    ev_pred = _safe_float(ml_latest.get("ev_pred"))
    ev_true = _safe_float(ml_latest.get("ev_true"))

    shape_entry_k = _safe_float(ml_latest.get("shape_entry_k"))
    shape_rr_target = _safe_float(ml_latest.get("shape_rr_target"))
    shape_tp_k = _safe_float(ml_latest.get("shape_tp_k"))
    shape_sl_k = _safe_float(ml_latest.get("shape_sl_k"))

    # shape の説明（初心者向け：一発で分かる）
    # entry_k: 0.0=補正なし / 大きいほど押し目寄せ（今回の設計）
    shape_entry_comment = [
        "Entry補正（ATR係数）：エントリー価格を“押し目寄せ”する強さ",
        "0.0 = 補正なし（素のEntry）",
        "大きいほど“飛びつき”を抑えて、有利な位置に寄せる方向",
    ]
    rr_comment = [
        "RRターゲット：狙うリスクリワード（大きいほど利確が遠い）",
        "大きいほど『当たりにくいが当たると大きい』方向",
        "p_tp_first が低いとRRを上げて“無理に撃たない”設計",
    ]
    tp_sl_comment = [
        "TP係数/SL係数：ATRに対する利確幅/損切幅の倍率",
        "TPが大きいほど利確は遠く、SLが大きいほど損切は広い",
        "この2つの比で“実質RR”が決まる",
    ]

    cards: List[Dict[str, Any]] = []

    cards.append(
        {
            "title": "p_win（勝つ確率）",
            "value": _as_raw_str(ml_latest.get("p_win")),
            "direction": "up",
            "signal": _signal_for_prob(p_win),
            "explain_lines": [
                "この1回の“勝ちやすさ”の推定（0〜1）",
                "0.50は五分。0.55超でも“過信禁止”（参考）",
                "勝率そのものではなく『勝ちやすい条件っぽさ』",
            ],
            "units_hint": "",
        }
    )
    cards.append(
        {
            "title": "p_tp_first（TP先）",
            "value": _as_raw_str(ml_latest.get("p_tp_first")),
            "direction": "up",
            "signal": _signal_for_prob(p_tp_first),
            "explain_lines": [
                "この1回が『先に利確に届きそうか』の推定（0〜1）",
                "高いほど“利確先行”の読み。低いほど“損切先行”寄り",
                "Shape（Entry/RR/TP/SL）補正の材料になる",
            ],
            "units_hint": "",
        }
    )
    cards.append(
        {
            "title": "EV_pred（予測EV）",
            "value": _as_raw_str(ml_latest.get("ev_pred")),
            "direction": "up",
            "signal": _signal_for_ev(ev_pred),
            "explain_lines": [
                "MLが見積もった“期待値”のスコア（単位は内部定義）",
                "プラスなら期待値あり。マイナスなら基本は見送り寄り",
                "これは“形に反映前”の値",
            ],
            "units_hint": "",
        }
    )
    cards.append(
        {
            "title": "EV_true（形に反映後）",
            "value": _as_raw_str(ml_latest.get("ev_true")),
            "direction": "up",
            "signal": _signal_for_ev(ev_true),
            "explain_lines": [
                "Shape補正（Entry/RR/TP/SL）を入れた後の“最終期待値”",
                "0以下なら『この形だと旨味が薄い』→ qtyが0寄りになることもある",
                "ここが“最終判断”に近い",
            ],
            "units_hint": "",
        }
    )

    # shape cards（表示はそのまま、丸めない）
    cards.append(
        {
            "title": "Entry補正（ATR係数）",
            "value": _as_raw_str(ml_latest.get("shape_entry_k")),
            "direction": "up",
            "signal": "yellow" if shape_entry_k is not None else "na",
            "explain_lines": shape_entry_comment,
            "units_hint": "",
        }
    )
    cards.append(
        {
            "title": "RRターゲット",
            "value": _as_raw_str(ml_latest.get("shape_rr_target")),
            "direction": "up",
            "signal": "yellow" if shape_rr_target is not None else "na",
            "explain_lines": rr_comment,
            "units_hint": "",
        }
    )
    cards.append(
        {
            "title": "TP係数 / SL係数",
            "value": f"{_as_raw_str(ml_latest.get('shape_tp_k'))} / {_as_raw_str(ml_latest.get('shape_sl_k'))}",
            "direction": "up",
            "signal": "yellow" if (shape_tp_k is not None or shape_sl_k is not None) else "na",
            "explain_lines": tp_sl_comment,
            "units_hint": "",
        }
    )

    return cards


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
    - behavior/latest_behavior.jsonl を読む（最新ML推論の実数値用）
    - behavior/ticker/latest_ticker_u{user}.json を読む
    - ml/models/latest/meta.json を読む（AUC/RMSEなど）
    - ✅ 初心者用：信号機＋説明カードに変換（丸めない）
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

    # ✅ 初心者用：信号機＋説明カード
    ml_metric_cards = _build_ml_metric_cards(ml_metrics)

    # ★ 最新ML推論（実数値）
    dataset_rows = _read_jsonl(dataset_path)
    raw_latest = _select_latest_ml_inference(dataset_rows, user_id=user.id)
    ml_latest = _ml_latest_view_from_raw(raw_latest) if raw_latest else None

    # ✅ 初心者用：最新推論カード（信号機＋説明）
    ml_latest_cards = _build_ml_latest_cards(ml_latest)

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
                "ml_metric_cards": ml_metric_cards,

                "ml_latest": ml_latest,
                "ml_latest_cards": ml_latest_cards,
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
        "ml_metric_cards": ml_metric_cards,

        "ml_latest": ml_latest,
        "ml_latest_cards": ml_latest_cards,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)