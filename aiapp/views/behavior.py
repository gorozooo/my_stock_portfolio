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

# ★ policy_loader を使って runtime 優先で読む（Cのレバー表示用）
from aiapp.services.policy_loader import load_short_aggressive_policy


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


# =========================
# ★ policy helpers（C: レバー化）
# =========================

def _get_in(d: Any, path: List[str]) -> Any:
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _f(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _load_policy_snapshot() -> Dict[str, Any]:
    """
    runtime 優先の policy を読み、画面に出したい“現在値”を整形して返す。
    """
    try:
        pdata = load_short_aggressive_policy() or {}
        if not isinstance(pdata, dict):
            pdata = {}
    except Exception:
        pdata = {}

    learn_mode = None
    try:
        learn_mode = str(_get_in(pdata, ["pro", "learn_mode"]) or "").strip().lower() or None
    except Exception:
        learn_mode = None

    # tighten は learn_mode があればそこ優先（無ければ None）
    tighten = None
    if learn_mode:
        t = _get_in(pdata, ["pro", "profiles", learn_mode, "tighten"])
        if isinstance(t, dict):
            tighten = t

    filters = pdata.get("filters") if isinstance(pdata.get("filters"), dict) else {}
    fees = pdata.get("fees") if isinstance(pdata.get("fees"), dict) else {}

    # min_* は tighten -> filters -> default
    min_net_profit_yen = _f((tighten or {}).get("min_net_profit_yen", filters.get("min_net_profit_yen", 1000.0)), 1000.0)
    min_reward_risk = _f((tighten or {}).get("min_reward_risk", filters.get("min_reward_risk", 1.0)), 1.0)

    commission_rate = _f(fees.get("commission_rate", 0.0005), 0.0005)
    min_commission = _f(fees.get("min_commission", 100.0), 100.0)
    slippage_rate = _f(fees.get("slippage_rate", 0.001), 0.001)

    return {
        "learn_mode": learn_mode or "",
        "min_net_profit_yen": float(min_net_profit_yen),
        "min_reward_risk": float(min_reward_risk),
        "commission_rate": float(commission_rate),
        "min_commission": float(min_commission),
        "slippage_rate": float(slippage_rate),
    }


def _make_hypothesis_levers(
    *,
    wl_total: int,
    win_rate: Optional[float],
    avg_r: Optional[float],
    avg_pl: Optional[float],
    labels: Dict[str, int],
    streak_label: str,
    streak_len: int,
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    “仮説”を文章で終わらせず、
    - どの数値（レバー）が原因っぽいか
    - 今の値
    - どう動かすと何が変わるか
    を dict で返す。
    """
    out: List[Dict[str, Any]] = []

    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    win = int(labels.get("win", 0))
    lose = int(labels.get("lose", 0))

    min_net = float(policy.get("min_net_profit_yen") or 1000.0)
    min_rr = float(policy.get("min_reward_risk") or 1.0)
    slip = float(policy.get("slippage_rate") or 0.001)
    comm_rate = float(policy.get("commission_rate") or 0.0005)
    min_comm = float(policy.get("min_commission") or 100.0)

    # ① データ不足
    if wl_total < 5:
        out.append(
            {
                "title": "WLが少なく“型”を確定できない",
                "why": f"win/lose の母数が {wl_total} 件。統計がブレる段階。",
                "lever": "data.wl_total",
                "current": wl_total,
                "action": "同一モード・同一ルールで WL を 8件以上に増やす",
                "effect": "勝率/平均Rが安定し、entry_reason別の差が見え始める",
            }
        )

    # ② 命中が低い
    if win_rate is not None and wl_total >= 5 and win_rate < 45:
        out.append(
            {
                "title": "命中が弱い → “入らない条件”が先",
                "why": f"勝率={win_rate:.1f}%（WL={wl_total}）。入り方の選別がズレている可能性。",
                "lever": "entry.filters / NG_conditions",
                "current": "—",
                "action": "LOSE連続の entry_reason / 条件を固定してNG化（同条件は入らない）",
                "effect": "勝率の底上げ（まず“負け筋”を潰す）",
            }
        )

    # ③ 平均Rがマイナス（撤退が深い/滑り/ロット）
    if avg_r is not None and wl_total >= 5 and avg_r < 0:
        out.append(
            {
                "title": "平均Rがマイナス → 負けが想定より深い",
                "why": f"平均R={avg_r:+.3f}。滑り/我慢/ロットのどれかが混ざる典型。",
                "lever": "policy.fees.slippage_rate",
                "current": slip,
                "action": "（検証）slippage_rate を現実に寄せる + LOSEの実滑りを side に保存して見える化",
                "effect": "“見かけ勝ち”を排除し、EV_true が現実と一致する",
            }
        )
        out.append(
            {
                "title": "損切りの効きが弱い可能性（RR以前の問題）",
                "why": "Rが崩れてる時は、TP/SLより“SLの守り方”が原因になりやすい。",
                "lever": "execution.discipline (SL obey)",
                "current": "—",
                "action": "LOSEだけ抽出して、予定SL→実際の損失R の乖離を記録",
                "effect": "“ルール通りに切れてない”が数値で確定する",
            }
        )

    # ④ skip/carry 多すぎ → フィルター厳しすぎ or 利益が薄い
    if (carry + skip) >= (win + lose) and (carry + skip) >= 3:
        out.append(
            {
                "title": "見送り/継続が多い → フィルターが硬すぎる可能性",
                "why": f"carry+skip={carry+skip} / win+lose={win+lose}。採用条件が通りにくい。",
                "lever": "policy.filters.min_net_profit_yen",
                "current": int(min_net),
                "action": "（一時検証）min_net_profit_yen を 20〜30%下げた場合の採用数/勝率変化を見る",
                "effect": "“厳しすぎて機会損失”か、“厳しいほど良い”かが判定できる",
            }
        )
        out.append(
            {
                "title": "RRフィルターが厳しすぎる可能性",
                "why": f"min_reward_risk={min_rr:.2f}。p_tp_firstが低い銘柄は RR を上げにいくので弾かれやすい。",
                "lever": "policy.filters.min_reward_risk",
                "current": float(min_rr),
                "action": "（検証）min_reward_risk を少しだけ下げ、採用銘柄の“実R”が改善するかを見る",
                "effect": "“理想RR”より“実際の勝ち筋”を優先できる",
            }
        )

    # ⑤ 勝率は高いのに平均Rが弱い → 利確が早い/伸ばせてない
    if win_rate is not None and avg_r is not None and wl_total >= 8:
        if win_rate >= 60 and avg_r < 0.2:
            out.append(
                {
                    "title": "命中は高いがRが伸びない → 利確が早い",
                    "why": f"勝率={win_rate:.1f}% / 平均R={avg_r:+.3f}",
                    "lever": "entry_service.tp_k (RR target)",
                    "current": "—",
                    "action": "p_tp_firstが高い局面だけ RR_target を下げ、早利確の癖を抑える（=伸ばす枠を作る）",
                    "effect": "勝率を落とさず平均Rを上げる方向に寄る",
                }
            )

    # ⑥ 直近の連続
    if streak_len >= 2 and streak_label in ("win", "lose"):
        if streak_label == "win":
            out.append(
                {
                    "title": f"WINが {streak_len} 連続 → 勝ち条件を固定するチャンス",
                    "why": "連続時は“同条件の再現”が最も効く局面。",
                    "lever": "pattern.freeze (entry_reason / regime)",
                    "current": "—",
                    "action": "WINの entry_reason 上位2つを“優先ルール”として固定し、同条件だけ増殖",
                    "effect": "再現性スコアが上がり、⭐️も安定しやすい",
                }
            )
        else:
            out.append(
                {
                    "title": f"LOSEが {streak_len} 連続 → NG条件を固定するチャンス",
                    "why": "連続時は“同じ負け筋”が繰り返されている可能性が高い。",
                    "lever": "ng.freeze (entry_reason / zone)",
                    "current": "—",
                    "action": "LOSEの entry_reason と直前条件を固定し、同条件は入らない（まず被弾停止）",
                    "effect": "負けの連鎖を止めて、学習の前提が整う",
                }
            )

    # 手数料も明示で1つ入れておく（見える化）
    out.append(
        {
            "title": "コスト前提（Policy）",
            "why": "採用/見送りの“純利益”に直撃するので固定表示しておく。",
            "lever": "policy.fees",
            "current": f"commission_rate={comm_rate} / min_commission={int(min_comm)} / slippage_rate={slip}",
            "action": "現実とズレてたら修正（見かけのEVを排除）",
            "effect": "EV_true と実運用の差が減る",
        }
    )

    return out[:8]


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

    # ★ policy snapshot（runtime 優先）
    policy_snapshot = _load_policy_snapshot()

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
                "policy": policy_snapshot,
            },
        )

    understanding_label = _understanding_label(wl_total)

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

    # ★ C：仮説をレバーに変換
    hypotheses = _make_hypothesis_levers(
        wl_total=wl_total,
        win_rate=win_rate,
        avg_r=avg_r,
        avg_pl=avg_pl,
        labels=labels,
        streak_label=streak_label,
        streak_len=streak_len,
        policy=policy_snapshot,
    )

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
        "policy": policy_snapshot,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)