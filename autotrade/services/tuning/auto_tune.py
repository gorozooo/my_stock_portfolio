"""
[FILE] autotrade/services/tuning/auto_tune.py
[PATH] <project_root>/autotrade/services/tuning/auto_tune.py

このファイルは何？
- 設計V/W：自動チューニング（最小構成）の本体です。
- “毎朝ちょっとだけ”候補設定（CANDIDATE Snapshot）を作り、同日バックテスト（Execution）で検証します。
- 採用（ACTIVE昇格）は別部品 auto_promote.py が担当（= 世代交代＆証拠焼き付け）。

最小構成ルール（固定）：
- 1日に動かすノブは1つだけ（原因不明を防ぐ）
- 変更幅は小さく固定（暴走防止）
- 悪化したら即棄却（前Snapshot維持）
- FULLを目指す（STOP→LIGHT、LIGHT→FULL はOK / 逆はNG）

注意：
- gate判定ロジック（Z）は gate.py を正として使う（変更しない）
- ここは「候補作成＆検証」まで。AUTO昇格は auto_promote.py がやる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import date as dt_date

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe


# ---- gateレベルの序列（比較用）----
_GATE_RANK = {"STOP": 0, "LIGHT": 1, "FULL": 2}


@dataclass(frozen=True)
class AutoTuneResult:
    ok: bool
    skipped: bool
    reason: str
    created_candidate_id: Optional[int]
    knob: Optional[str]
    base: Dict[str, Any]
    cand: Dict[str, Any]


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _get_gate_bundle_from_state(state: AutoTradeDailyState) -> Dict[str, Any]:
    """
    runner.py が作った新フォーマットの gate を読む。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    return {
        "final_level": str(final.get("gate_level") or state.gate_level or "STOP"),
        "active": list(final.get("active") or []),
        "disabled": list(final.get("disabled") or []),
        "vwap": gate.get("VWAP") if isinstance(gate.get("VWAP"), dict) else {},
        "breakout": gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {},
    }


def _get_rr_from_state(state: AutoTradeDailyState) -> Tuple[float, float]:
    """
    rrは state.backtest['meta'] を優先。無ければsettings。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}

    rr_b = _safe_float(meta.get("rr_breakout"), _safe_float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0), 2.0))
    rr_v = _safe_float(meta.get("rr_vwap"), _safe_float(getattr(settings, "AUTOTRADE_RR_VWAP", 1.5), 1.5))
    return rr_b, rr_v


def _get_baseline_metrics_from_state(state: AutoTradeDailyState, *, windows: List[int]) -> Dict[str, Any]:
    """
    baseline は、朝の runner が既に作った state.backtest['by_window'] を読む（再計算しない）。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    # by_window は {20: {"VWAP": {...}, "BREAKOUT": {...}}} か、
    # JSONField経由でキーが文字列になるケースもあるので両対応。
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for w in windows:
        w_int = int(w)
        w_key_s = str(w_int)
        w_dict = None
        if isinstance(by_window.get(w_int), dict):
            w_dict = by_window.get(w_int)
        elif isinstance(by_window.get(w_key_s), dict):
            w_dict = by_window.get(w_key_s)
        else:
            w_dict = {}

        out[w_int] = {
            "VWAP": w_dict.get("VWAP") if isinstance(w_dict.get("VWAP"), dict) else {},
            "BREAKOUT": w_dict.get("BREAKOUT") if isinstance(w_dict.get("BREAKOUT"), dict) else {},
        }

    return out


def _aggregate_score(metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]], *, windows: List[int]) -> Dict[str, Any]:
    """
    Z準拠：PF/DD/損益/回数の“4本柱”をまとめて比較しやすい形にする。
    ここでは「VWAPの20/60を主軸」にして baseline vs candidate の差分を評価する。
    （VWAPが止まると最終STOPになり得るため）
    """
    pnl = 0
    dd_pct = 0.0
    pf = 0.0
    trades = 0

    # VWAP中心で集計（windows合算）
    for w in windows:
        m = ((metrics_by_window.get(int(w)) or {}).get("VWAP") or {})
        pnl += _safe_int(m.get("total_pnl"), 0)
        dd_pct = max(dd_pct, _safe_float(m.get("max_drawdown_pct"), 0.0))
        pf += _safe_float(m.get("profit_factor"), 0.0)
        trades += _safe_int(m.get("trades"), 0)

    # PFは単純合算より平均が分かりやすい
    pf_avg = pf / max(1, len(windows))

    return {
        "pnl_sum_yen": int(pnl),
        "max_dd_pct": float(dd_pct),
        "pf_avg": float(pf_avg),
        "trades_sum": int(trades),
    }


def _is_improvement(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """
    改善条件（最小・安全寄り）
    - gateが上がる（STOP→LIGHT、LIGHT→FULL）なら即OK
    - gateが同じなら：
      * 損益↑ を基本
      * DD悪化しない（+0.3%以内）
      * PF悪化しない
      * tradesが極端に減らない（ベースの70%未満はNG）
    """
    b_gate = str(base.get("gate_level") or "STOP")
    c_gate = str(cand.get("gate_level") or "STOP")

    if _GATE_RANK.get(c_gate, 0) > _GATE_RANK.get(b_gate, 0):
        return True

    if _GATE_RANK.get(c_gate, 0) < _GATE_RANK.get(b_gate, 0):
        return False

    # 同じゲートなら中身で
    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_pnl = _safe_int(b.get("pnl_sum_yen"), 0)
    c_pnl = _safe_int(c.get("pnl_sum_yen"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 1.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 1.0)

    b_pf = _safe_float(b.get("pf_avg"), 0.0)
    c_pf = _safe_float(c.get("pf_avg"), 0.0)

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    # tradesが激減してるのはノイズ化の危険
    if b_tr > 0 and c_tr < int(b_tr * 0.70):
        return False

    # DD悪化（+0.3%超）はNG
    if (c_dd - b_dd) > 0.003:
        return False

    # PF悪化はNG
    if c_pf + 1e-9 < b_pf:
        return False

    # 損益が増えたらOK（最重要）
    if c_pnl > b_pnl:
        return True

    # 損益横ばいならPFが上がってDD横ばいならOK（弱い改善）
    if c_pnl == b_pnl and (c_pf > b_pf) and (c_dd <= b_dd + 1e-9):
        return True

    return False


def _pick_knob_and_candidates(state: AutoTradeDailyState) -> List[Dict[str, Any]]:
    """
    1日1ノブだけ動かす。優先順位（固定）：
    1) VWAPがSTOP → rr_vwap を微調整
    2) BREAKOUTがFULLでない（VWAPはSTOPでない）→ rr_breakout を微調整
    3) それ以外 → 今日は触らない
    """
    gate = _get_gate_bundle_from_state(state)
    lv_v = str((gate.get("vwap") or {}).get("gate_level") or "STOP")
    lv_b = str((gate.get("breakout") or {}).get("gate_level") or "STOP")

    rr_b, rr_v = _get_rr_from_state(state)
    step = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP", 0.1))
    rr_min = float(getattr(settings, "AUTOTRADE_TUNE_RR_MIN", 1.0))
    rr_max = float(getattr(settings, "AUTOTRADE_TUNE_RR_MAX", 3.0))

    def clamp(x: float) -> float:
        return max(rr_min, min(rr_max, float(x)))

    cands: List[Dict[str, Any]] = []

    if lv_v == "STOP":
        # VWAP救済を優先
        cands.append({"knob": "rr_vwap", "rr_breakout": rr_b, "rr_vwap": clamp(rr_v - step), "delta": -step})
        cands.append({"knob": "rr_vwap", "rr_breakout": rr_b, "rr_vwap": clamp(rr_v + step), "delta": +step})
        return cands

    # VWAPが生きてるなら、FULL化のためBREAKOUTを狙う
    if lv_b != "FULL":
        cands.append({"knob": "rr_breakout", "rr_breakout": clamp(rr_b - step), "rr_vwap": rr_v, "delta": -step})
        cands.append({"knob": "rr_breakout", "rr_breakout": clamp(rr_b + step), "rr_vwap": rr_v, "delta": +step})
        return cands

    return []


@transaction.atomic
def auto_tune_generate_candidate(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> AutoTuneResult:
    """
    朝フローで呼ぶ入口。
    - 今日の state（朝の runner 実行後）を前提に、候補を作って検証する。
    - 良い候補があれば CANDIDATE Snapshot を1つだけ作る（最初の合格を採用）。

    注意：
    - 既に今日CANDIDATEがある場合は増殖させない（暴走防止）。
    """
    if target_date is None:
        target_date = timezone.localdate()

    if windows is None:
        windows = list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60]))

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return AutoTuneResult(
            ok=True, skipped=True, reason="emergency_stop",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # 今日のCANDIDATEが既にあるなら作らない（最小）
    exists_today_candidate = AutoTradeSettingSnapshot.objects.filter(
        status="CANDIDATE",
        created_at__date=target_date,
    ).exists()
    if exists_today_candidate:
        return AutoTuneResult(
            ok=True, skipped=True, reason="candidate_already_exists_today",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # ACTIVE Snapshot
    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )
    if not active:
        return AutoTuneResult(
            ok=False, skipped=True, reason="no_active_snapshot",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # picks（朝universe）
    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_picks",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # baseline（朝runner結果を読む）
    gate_bundle = _get_gate_bundle_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or state.gate_level or "STOP"),
        "score": base_score,
        "rr": {"BREAKOUT": _get_rr_from_state(state)[0], "VWAP": _get_rr_from_state(state)[1]},
    }

    # 1ノブ候補を作る
    knobs = _pick_knob_and_candidates(state)
    if not knobs:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_tunable_knob_today",
            created_candidate_id=None, knob=None, base=base_pack, cand={}
        )

    # 候補を評価して、最初の合格だけ採用
    for k in knobs:
        knob = str(k.get("knob") or "")
        rr_b = float(k.get("rr_breakout"))
        rr_v = float(k.get("rr_vwap"))

        # candidate snapshot 作成（中身はACTIVEをコピー + tune情報を焼き込み）
        snap_dict = active.snapshot if isinstance(active.snapshot, dict) else {}
        new_snap = dict(snap_dict)
        new_snap["tune"] = {
            "target_date": str(target_date),
            "knob": knob,
            "delta": float(k.get("delta") or 0.0),
            "rr_breakout": float(rr_b),
            "rr_vwap": float(rr_v),
            "based_on_active_id": int(active.id),
            "note": "auto_tune_generate_candidate",
        }

        label = f"AUTO_TUNE {target_date} {knob} ({k.get('delta'):+.2f})"
        cand = AutoTradeSettingSnapshot.objects.create(
            user=active.user,
            source_profile=active.source_profile,
            label=label,
            status="CANDIDATE",
            snapshot=new_snap,
        )

        # 同日 backtest を candidate で実行（Execution生成）
        # - force=True でその日の候補検証を確実に作る
        res = run_detailed_backtests_for_universe(
            snapshot=cand,
            picks=picks,
            target_date=target_date,
            windows=tuple(int(x) for x in windows),
            rr_breakout=float(rr_b),
            rr_vwap=float(rr_v),
            base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
            force=True,
        )

        # candidateの結果を state から読むのではなく res.metrics から読む（確実）
        cand_metrics = (res.get("metrics") or {}) if isinstance(res, dict) else {}
        # cand_metrics は {window: {strategy: metrics}} 形式のはず（runnerの返り値）
        # windowキーがstrの場合も考慮して正規化
        norm: Dict[int, Dict[str, Dict[str, Any]]] = {}
        for w in windows:
            w_int = int(w)
            d = cand_metrics.get(w_int) if isinstance(cand_metrics.get(w_int), dict) else cand_metrics.get(str(w_int))
            d = d if isinstance(d, dict) else {}
            norm[w_int] = {
                "VWAP": d.get("VWAP") if isinstance(d.get("VWAP"), dict) else {},
                "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
            }

        cand_score = _aggregate_score(norm, windows=list(windows))

        # runnerのres.gate.final を使う
        gate = (res.get("gate") or {}) if isinstance(res, dict) else {}
        final_level = str(gate.get("final") or "STOP")

        cand_pack = {
            "gate_level": final_level,
            "score": cand_score,
            "rr": {"BREAKOUT": rr_b, "VWAP": rr_v},
        }

        # 改善判定
        if _is_improvement(base_pack, cand_pack):
            # 合格：cand は残す（CANDIDATEのまま）→ auto_promote が評価＆昇格
            return AutoTuneResult(
                ok=True, skipped=False, reason="candidate_created",
                created_candidate_id=int(cand.id), knob=knob, base=base_pack, cand=cand_pack
            )

        # 不合格：作った候補は即RETIREDにして残骸を残しすぎない
        cand.status = "RETIRED"
        cand.snapshot = {**(cand.snapshot if isinstance(cand.snapshot, dict) else {}), "tune_rejected": True}
        cand.save(update_fields=["status", "snapshot"])

    return AutoTuneResult(
        ok=True, skipped=True, reason="no_improving_candidate",
        created_candidate_id=None, knob=None, base=base_pack, cand={}
    )