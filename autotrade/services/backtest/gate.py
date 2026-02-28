"""
[FILE] autotrade/services/backtest/gate.py
[PATH] <project_root>/autotrade/services/backtest/gate.py

このファイルは何？
- バックテスト結果（20/60/120）を見て、🟢🟡🔴を決める判定部品です。
- “合格ライン（GATE_THRESHOLDS）” を基準に、判定（FULL/LIGHT/STOP）と初心者向けの理由文（reasons）を生成します。

今回の変更点（初心者UI向け）：
- reasons を「/区切り」ではなく、短い箇条書きの日本語に統一
- 20日/60日など “期間ごと” に
  1) ひとことで（意味）
  2) どこが未達か（基準と比較）
  3) 数字（PF/DD/回数/勝率/損益）
  を並べる
- min_win_rate（勝率基準）を閾値として正式に扱えるようにする（無い場合は無視）
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple


# 攻め型（PF重視）
# - BREAKOUTは勝率が高くなくてもPFで勝つ戦略なので、PFを強めに見る
# - ただし「回数が少ないPF」は信用できないので min_trades を上げる
# - DDは多少許容するが、破滅DDは止める
GATE_THRESHOLDS = {
    "FULL": {
        "max_dd_pct": 0.03,      # 3%
        "min_pf": 1.10,          # 強化
        "min_trades": 12,        # 強化（薄い成績での誤判定を避ける）
        "min_win_rate": 0.42,    # 追加（42%）
    },
    "LIGHT": {
        "max_dd_pct": 0.05,      # 5%
        "min_pf": 1.04,          # 少し強化
        "min_trades": 8,         # 少し強化
        "min_win_rate": 0.38,    # 追加（38%）
    },
}


def _yen(x: Any) -> str:
    try:
        return f"{int(x):,}円"
    except Exception:
        return "0円"


def _pct(x: Any, digits: int = 1) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except Exception:
        return "0.0%"


def _safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _get_threshold(level: str, key: str, default: Any = None) -> Any:
    d = GATE_THRESHOLDS.get(level) or {}
    return d.get(key, default)


def _make_window_reasons(
    *,
    window_days: int,
    level: str,
    metrics: Dict[str, Any],
    threshold_level: str,
) -> List[str]:
    """
    期間ごとの “初心者向け” 理由文を作る。
    - / 区切り禁止
    - 1行が長くならないようにする
    """
    dd_pct = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    dd_yen = _safe_int(metrics.get("max_drawdown_yen"), 0)
    total_pnl = _safe_int(metrics.get("total_pnl"), 0)

    wins = _safe_int(metrics.get("wins"), 0)
    losses = _safe_int(metrics.get("losses"), 0)

    # 勝率（wins/trades優先）
    win_rate = (wins / trades) if trades > 0 else 0.0

    # 基準（このwindowの判定に使ったレベルの基準を表示）
    th_pf = _safe_float(_get_threshold(threshold_level, "min_pf", 0.0), 0.0)
    th_dd = _safe_float(_get_threshold(threshold_level, "max_dd_pct", 1.0), 1.0)
    th_tr = _safe_int(_get_threshold(threshold_level, "min_trades", 0), 0)
    th_wr = _get_threshold(threshold_level, "min_win_rate", None)

    unmet: List[str] = []
    if pf + 1e-12 < th_pf:
        unmet.append(f"PFが弱い（基準：{th_pf:.2f}以上）")
    if dd_pct - 1e-12 > th_dd:
        unmet.append(f"最大落ち込みが大きい（基準：{_pct(th_dd)}以内）")
    if trades < th_tr:
        unmet.append(f"取引回数が少ない（基準：{th_tr}回以上）")
    if th_wr is not None and win_rate + 1e-12 < float(th_wr):
        unmet.append(f"勝率が低い（基準：{_pct(float(th_wr))}以上）")

    # ひとことで（意味）
    if level == "FULL":
        one = "安定しているので通常稼働OKです。"
    elif level == "LIGHT":
        one = "悪くはないが不安要素あり。軽稼働が安全です。"
    else:
        # STOP
        if total_pnl > 0 and pf >= 1.0:
            one = "短期は悪くないが、基準未達があり停止が安全です。"
        else:
            one = "成績が不安定なので停止が安全です。"

    lines: List[str] = []
    lines.append(f"【{window_days}日】判定：{level}")
    lines.append(f"ひとことで：{one}")

    if unmet:
        lines.append("未達のポイント：")
        for u in unmet:
            lines.append(f"  - {u}")
    else:
        lines.append("未達のポイント：なし（基準クリア）")

    # 数字（初心者向けに意味ラベル付き）
    lines.append(f"損益：{_yen(total_pnl)}")
    lines.append(f"PF（勝ち合計÷負け合計）：{pf:.2f}")
    lines.append(f"最大落ち込み（DD）：-{_yen(abs(dd_yen))}（-{_pct(dd_pct)}）")
    if trades > 0:
        lines.append(f"取引回数：{trades}回（勝{wins} / 負{losses}）")
        lines.append(f"勝率：{_pct(win_rate, digits=1)}")
    else:
        lines.append("取引回数：0回（評価不可）")

    return lines


def _judge_single(metrics: Dict[str, Any], *, window_days: int) -> Tuple[str, List[str]]:
    """
    metrics には summarize_executions の戻り値（trades/PF/DD/円分解）が入る想定。
    返り値：("FULL"/"LIGHT"/"STOP", reasons[])
    """
    dd = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    wins = _safe_int(metrics.get("wins"), 0)
    win_rate = (wins / trades) if trades > 0 else 0.0

    # FULL判定
    full = GATE_THRESHOLDS["FULL"]
    full_dd = _safe_float(full.get("max_dd_pct"), 1.0)
    full_pf = _safe_float(full.get("min_pf"), 0.0)
    full_tr = _safe_int(full.get("min_trades"), 0)
    full_wr = full.get("min_win_rate", None)

    full_ok = (dd <= full_dd) and (pf >= full_pf) and (trades >= full_tr)
    if full_wr is not None:
        full_ok = full_ok and (win_rate >= float(full_wr))

    if full_ok:
        reasons = _make_window_reasons(
            window_days=window_days,
            level="FULL",
            metrics=metrics,
            threshold_level="FULL",
        )
        return "FULL", reasons

    # LIGHT判定
    light = GATE_THRESHOLDS["LIGHT"]
    light_dd = _safe_float(light.get("max_dd_pct"), 1.0)
    light_pf = _safe_float(light.get("min_pf"), 0.0)
    light_tr = _safe_int(light.get("min_trades"), 0)
    light_wr = light.get("min_win_rate", None)

    light_ok = (dd <= light_dd) and (pf >= light_pf) and (trades >= light_tr)
    if light_wr is not None:
        light_ok = light_ok and (win_rate >= float(light_wr))

    if light_ok:
        reasons = _make_window_reasons(
            window_days=window_days,
            level="LIGHT",
            metrics=metrics,
            threshold_level="LIGHT",
        )
        return "LIGHT", reasons

    # STOP
    reasons = _make_window_reasons(
        window_days=window_days,
        level="STOP",
        metrics=metrics,
        threshold_level="LIGHT",  # STOP時も「最低合格ライン」はLIGHTを見せる
    )
    return "STOP", reasons


def judge_multi_window(metrics_by_window: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """
    20/60/120 をまとめて判定して、最終の gate_level と理由を返す
    """
    detail: Dict[int, str] = {}
    reasons: List[str] = []

    for w, m in sorted(metrics_by_window.items(), key=lambda x: x[0]):
        level, rs = _judge_single(m, window_days=int(w))
        detail[int(w)] = level

        # rsは “期間ごとの箇条書き” なので、そのまま積む
        reasons.extend(rs)

        # 期間ごとの区切り（読みやすさ）
        reasons.append("")

    # 末尾の空行を整理
    while reasons and (reasons[-1] or "").strip() == "":
        reasons.pop()

    # final判定（既存思想）
    if detail.get(20) == "STOP":
        final_level = "STOP"
        final_reasons = ["直近（20日）が不安定なため、停止が安全です。"]
    elif any(detail.get(w) == "STOP" for w in (60, 120)):
        final_level = "LIGHT"
        final_reasons = ["中長期に不安があるため、軽稼働が安全です。"]
    else:
        final_level = "FULL"
        final_reasons = ["すべての期間で基準を満たしています。通常稼働OKです。"]

    return {
        "gate_level": final_level,
        "reasons": final_reasons,
        "detail": detail,
        "reasons_verbose": reasons,  # ★初心者UI用（期間別の箇条書き）
    }


def gate_from_backtests(bt_by_window: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """
    旧 gate_service.gate_from_backtests の置き換え。
    """
    metrics_by_window: Dict[int, Dict[str, Any]] = {}
    for w in (20, 60, 120):
        d = (bt_by_window or {}).get(int(w)) or {}
        m = d.get("metrics") if isinstance(d, dict) else None
        metrics_by_window[int(w)] = (m if isinstance(m, dict) else d) or {}

    res = judge_multi_window(metrics_by_window)
    gate_level = str(res.get("gate_level") or "STOP")
    reason_text = "\n".join(res.get("reasons") or [])
    return gate_level, reason_text