from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

# コメント：VirtualTradeは今は直接使わないが、将来DB正の統合に備えて残す
from aiapp.models.vtrade import VirtualTrade  # noqa: F401


# =========================================================
# helpers
# =========================================================

def _safe_float(v: Any) -> Optional[float]:
    """コメント：None/空/文字列null を安全に float 化する"""
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """コメント：jsonファイルを読む（無ければNone）"""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """コメント：jsonlを読む（壊れた行はスキップ）"""
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
    コメント：simulate/sim_orders_*.jsonl を集計
    - qty_pro がある行のみ対象（PRO仕様）
    - eval_label_pro がある行を「評価済み」
    - win/loseのみの件数も wl としてカウント（勝率母数）
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

            # コメント：勝率母数は win/lose のみ
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
    """コメント：WL件数で“AIがどれくらい理解できてるか”を雑にラベル化"""
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
    コメント：
    - wl_labels は古→新（時系列）で入っている前提
    - 直近連続 streak を返す
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
    """コメント：直近12個だけ、W/L/F のピル表示用に整形"""
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
    """
    コメント：
    - “未来感”ではなく、あえてポップに尖った言い回しで出す
    - ただし内容はロジック由来（再現性ファースト）
    """
    hyps: List[str] = []

    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))
    win = int(labels.get("win", 0))
    lose = int(labels.get("lose", 0))

    # コメント：データ量の段階で言い方を変える
    if wl_total < 5:
        hyps.append("まだ“型”は確定できない。いまは《クセの芽》だけ採取してる。")
    else:
        hyps.append("型の輪郭が出てきた。次は《再現できる勝ち方》だけ残して磨く。")

    if win_rate is not None:
        if win_rate >= 60:
            hyps.append("命中は高い。敵は《勝ちが小さい/負けが大きい》の比率側にいる。")
        elif win_rate >= 45:
            hyps.append("命中は平均帯。改善は《入り方》より《撤退の形》の方が効く。")
        else:
            hyps.append("命中がまだ低い。条件が厳しすぎるか、刺さる前提がズレている。")

    if avg_r is not None:
        if avg_r >= 0.3:
            hyps.append("平均Rはプラス。利確の型を“コピー学習”していい段階。")
        elif avg_r >= 0:
            hyps.append("平均Rはゼロ付近。守れてるが、伸ばす学習が足りない。")
        else:
            hyps.append("平均Rがマイナス。負けが深い。ロット/滑り/我慢の混入を疑う。")

    if avg_pl is not None:
        if avg_pl >= 0:
            hyps.append("平均PLはプラス。次の敵は“大負け”より“取りこぼし”。")
        else:
            hyps.append("平均PLはマイナス。勝率より先に《負け平均サイズ》を潰すと立て直しが速い。")

    # コメント：carry/skipが多いと“撃たない癖”として示す
    if (carry + skip) >= (win + lose) and (carry + skip) >= 3:
        hyps.append("carry/skip が多い。あなたは“撃つ”より“様子見”で世界を制御してる。条件が固すぎるかも。")

    # コメント：連勝/連敗は学習上、最重要シグナル
    if streak_len >= 2 and streak_label in ("win", "lose"):
        if streak_label == "win":
            hyps.append(f"直近は WIN が {streak_len} 連続。同条件だけ増殖させたい（勝ち条件の固定）。")
        else:
            hyps.append(f"直近は LOSE が {streak_len} 連続。負け条件を固定して“入らないルール”にする。")

    return hyps[:6]


def _make_notes(wl_total: int, sim_total: int, eval_done: int, labels: Dict[str, int]) -> List[str]:
    """コメント：画面下の小メモ（集計の定義を誤解しないため）"""
    notes: List[str] = []
    notes.append(f"simulate（qty_proあり）={sim_total} 件 / 評価済み={eval_done} 件 / win-lose={wl_total} 件")
    if int(labels.get("carry", 0)) > 0:
        notes.append(f"carry={labels.get('carry', 0)} は“保有継続”なので、勝率の母数には入れていません。")
    if int(labels.get("skip", 0)) > 0:
        notes.append(f"skip={labels.get('skip', 0)} は“ポジション無し”なので、勝率の母数には入れていません。")
    return notes[:4]


def _make_bias_map(win_rate: Optional[float], avg_r: Optional[float], labels: Dict[str, int]) -> List[Dict[str, str]]:
    """
    コメント：
    - “判断DNA”として4項目に分解してポップ表示
    - ここはあくまで“見える化”で、学習ロジックそのものではない
    """
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
            risk = "撤退◎ / 回収◎"
        elif avg_r >= 0:
            risk = "撤退◯（概ねルール）"
        else:
            risk = "撤退×（深い）"

    if win_rate is None:
        hit = "未判定"
    else:
        if win_rate >= 60:
            hit = "命中 高"
        elif win_rate >= 45:
            hit = "命中 並"
        else:
            hit = "命中 低"

    if (carry + skip) == 0:
        indecision = "未判定"
    else:
        indecision = "見送り優位" if skip >= carry else "継続優位"

    return [
        {"name": "行動温度", "value": tempo},
        {"name": "撤退の質", "value": risk},
        {"name": "命中度", "value": hit},
        {"name": "迷いの形", "value": indecision},
    ]


def _make_wanted(wl_total: int, labels: Dict[str, int], avg_r: Optional[float], streak_label: str, streak_len: int) -> List[str]:
    """
    コメント：
    - “AIが次に欲しいデータ” を明確化（ユーザーの行動を誘導する）
    - ただの願望ではなく、今の不足に紐づけて出す
    """
    wanted: List[str] = []
    carry = int(labels.get("carry", 0))
    skip = int(labels.get("skip", 0))

    if wl_total < 8:
        wanted.append("同一ルール・同一モードでの連続トレード（WLを増やす）")
    if avg_r is not None and avg_r < 0:
        wanted.append("負けの直後の次の一手（取り返しに行く癖があるか）")
    if streak_len >= 2 and streak_label == "lose":
        wanted.append("LOSE連続中の条件を固定して“入らないルール”を作る（NG抽出）")
    if carry >= 2:
        wanted.append("carry の最終着地（利確/損切り/時間切れ）を増やす")
    if skip >= 2:
        wanted.append("見送った理由（なぜ入らなかったか）をメモに残すと学習が速い")

    if not wanted:
        wanted.append("今の勝ちパターンをもう一周（同条件で再現できるか）")

    return wanted[:6]


def _extract_recent_trades(side_rows: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """
    コメント：
    - latest_behavior_side.jsonl は “win/loseだけ” ではなく、表示用に直近をそのまま見せる
    - テンプレ側で色分けするため label を win/lose/flat に正規化
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

    # コメント：表示は古→新にしたいのでreverse
    out.reverse()
    return out


def _load_ticker(ticker_path: Path) -> Dict[str, Any]:
    """
    コメント：
    - media/aiapp/behavior/ticker/latest_ticker_u{user}.json
    - 無ければ空で返す（テンプレ側がダミー表示する）
    """
    j = _read_json(ticker_path)
    if not j:
        return {"date": "", "lines": []}

    lines = j.get("lines") or []
    if not isinstance(lines, list):
        lines = []

    lines = [str(x) for x in lines if str(x).strip()]
    return {"date": str(j.get("date") or ""), "lines": lines[:8]}


# =========================================================
# view
# =========================================================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ PRO仕様の行動ダッシュボード（ポップ版）
    - simulate/*.jsonl を集計（PRO: qty_pro + eval_label_pro）
    - behavior/model/latest_behavior_model_u{user}.json を読む
    - behavior/latest_behavior_side.jsonl を読む
    - behavior/ticker/latest_ticker_u{user}.json を読む（テロップ）
    """
    user = request.user
    today = timezone.localdate()
    today_label = today.strftime("%Y-%m-%d")

    # コメント：相対パス運用（プロジェクトルートで実行される前提）
    media_root = Path("media")
    sim_dir = media_root / "aiapp" / "simulate"
    beh_dir = media_root / "aiapp" / "behavior"

    model_path = beh_dir / "model" / f"latest_behavior_model_u{user.id}.json"
    side_path = beh_dir / "latest_behavior_side.jsonl"
    ticker_path = beh_dir / "ticker" / f"latest_ticker_u{user.id}.json"

    # コメント：simulate集計（=現状の“正”）
    sim_sum = _summarize_simulate_dir(sim_dir)

    # コメント：学習モデルの読み込み（無ければ空）
    model_json = _read_json(model_path) or {}
    has_model = bool(model_json)

    # コメント：モデル側KPI（入ってない/壊れてる可能性があるのでsafe）
    win_rate = _safe_float(model_json.get("win_rate"))
    avg_pl = _safe_float(model_json.get("avg_pl"))
    avg_r = _safe_float(model_json.get("avg_r"))
    total_trades = int(model_json.get("total_trades") or 0)

    # コメント：simulate側の確定カウント
    wl_total = int(sim_sum.get("wl") or 0)
    sim_total = int(sim_sum.get("total_qty") or 0)
    eval_done = int(sim_sum.get("eval_done") or 0)
    labels = sim_sum.get("labels") or {}

    # コメント：表示するかどうか（評価がゼロなら空状態）
    has_data = (wl_total >= 1) or (eval_done >= 1)

    # コメント：sideデータから直近並びを作る
    side_rows = _read_jsonl(side_path)
    wl_labels: List[str] = []
    for r in side_rows:
        lab = str(r.get("eval_label") or "").strip().lower()
        if lab in ("win", "lose", "flat"):
            wl_labels.append(lab)

    sequence = _make_sequence(wl_labels)
    streak_label, streak_len = _streak_from_labels(wl_labels)
    streak_text = "none" if streak_len == 0 else f"{streak_label.upper()} x{streak_len}"

    # コメント：ticker読み込み（無ければ空）
    ticker = _load_ticker(ticker_path)

    # コメント：理解度ラベルは WL件数ベース
    understanding_label = _understanding_label(wl_total)

    # 空状態でもテンプレが欲しがるキーは揃えて返す（表示崩れ防止）
    if not has_data:
        ctx = {
            "has_data": False,
            "today_label": today_label,
            "sim_files": int(sim_sum.get("files") or 0),
            "sim_total": sim_total,
            "wl_total": wl_total,
            "understanding_label": understanding_label,
            "win_rate": win_rate,
            "avg_pl": avg_pl,
            "avg_r": avg_r,
            "hypotheses": [],
            "notes": [],
            "bias_map": [],
            "wanted": [],
            "sequence": [],
            "streak_label": "none",
            "recent_trades": [],
            "model": {"has_model": has_model, "total_trades": total_trades},
            "ticker_date": ticker.get("date", ""),
            "ticker_lines": ticker.get("lines", []),
        }
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    # データがある場合：仮説/メモ/DNA/欲しいデータ/直近トレードを生成
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
    wanted = _make_wanted(wl_total=wl_total, labels=labels, avg_r=avg_r, streak_label=streak_label, streak_len=streak_len)
    recent_trades = _extract_recent_trades(side_rows, limit=8)

    ctx = {
        "has_data": True,
        "today_label": today_label,

        # コメント：ヘッダー系
        "sim_files": int(sim_sum.get("files") or 0),
        "sim_total": sim_total,
        "wl_total": wl_total,
        "understanding_label": understanding_label,

        # コメント：モデルKPI（無ければNoneのまま）
        "win_rate": win_rate,
        "avg_pl": avg_pl,
        "avg_r": avg_r,

        # コメント：分析ブロック
        "hypotheses": hypotheses,
        "notes": notes,
        "bias_map": bias_map,
        "wanted": wanted,

        # コメント：並び表示
        "sequence": sequence,
        "streak_label": streak_text,

        # コメント：観察ログ
        "recent_trades": recent_trades,

        # コメント：モデル存在
        "model": {"has_model": has_model, "total_trades": total_trades},

        # コメント：ticker
        "ticker_date": ticker.get("date", ""),
        "ticker_lines": ticker.get("lines", []),
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)