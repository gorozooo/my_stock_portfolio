# aiapp/services/pro_account.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from django.conf import settings


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _get(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    """
    dict のネストを "a.b.c" で取る。
    """
    cur: Any = d
    for key in path.split("."):
        if not isinstance(cur, dict):
            return default
        if key not in cur:
            return default
        cur = cur[key]
    return cur


def load_policy_yaml(path: str) -> Dict[str, Any]:
    """
    yml を読み込む。PyYAML が無い場合は例外。
    """
    try:
        import yaml  # type: ignore
    except Exception as e:
        raise RuntimeError("PyYAML が必要です（pip install pyyaml）") from e

    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"policy not found: {path}")

    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def _lot_size_for_code(code: str, policy: Dict[str, Any]) -> int:
    """
    ETF/ETN と株でロットを分ける（あなたの yml に合わせる）
    """
    code_s = (code or "").strip()
    etf_prefixes = _get(policy, "lot_rule.etf_codes_prefix", default=["13", "15"]) or ["13", "15"]
    etf_lot = int(_get(policy, "lot_rule.etf_lot", default=1) or 1)
    stock_lot = int(_get(policy, "lot_rule.stock_lot", default=100) or 100)

    if any(code_s.startswith(str(px)) for px in etf_prefixes):
        return max(1, etf_lot)
    return max(1, stock_lot)


@dataclass
class ProSizingResult:
    qty_pro: int
    required_cash_pro: float
    est_pl_pro: float
    est_loss_pro: float

    # フィルタ用の内部指標（デバッグしやすいように返す）
    rr: Optional[float] = None
    net_profit_yen: Optional[float] = None
    reason_skip: str = ""


def compute_pro_sizing_and_filter(
    *,
    code: str,
    side: str,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    policy: Dict[str, Any],
    total_equity_yen: Optional[float] = None,
) -> Tuple[Optional[ProSizingResult], str]:
    """
    PRO統一口座のサイズ・PL/Loss を計算し、ポリシー基準でフィルタ判定する。
    戻り:
      (result or None, reason)
    """
    side_u = (side or "BUY").upper()

    e = _to_float(entry)
    t = _to_float(tp)
    s = _to_float(sl)

    if e is None or t is None or s is None:
        return None, "missing_entry_tp_sl"

    if side_u != "BUY":
        # 今は BUY 前提（将来 SELL 拡張）
        return None, "side_not_supported"

    # 方向整合
    if not (s < e < t):
        return None, "invalid_price_relation"

    # ----- policy -----
    risk_pct = float(_get(policy, "risk_pct", default=1.0) or 1.0)  # % of equity
    credit_usage_pct = float(_get(policy, "credit_usage_pct", default=70.0) or 70.0)  # %
    commission_rate = float(_get(policy, "fees.commission_rate", default=0.0005) or 0.0005)
    min_commission = float(_get(policy, "fees.min_commission", default=100.0) or 100.0)
    slippage_rate = float(_get(policy, "fees.slippage_rate", default=0.001) or 0.001)

    min_net_profit_yen = float(_get(policy, "filters.min_net_profit_yen", default=2000.0) or 2000.0)
    min_reward_risk = float(_get(policy, "filters.min_reward_risk", default=1.0) or 1.0)
    allow_negative_pl = bool(_get(policy, "filters.allow_negative_pl", default=False))

    # equity（仮想）
    if total_equity_yen is None:
        # settings で上書きできるように（無ければ 3,000,000）
        total_equity_yen = float(getattr(settings, "AIAPP_PRO_EQUITY_YEN", 3_000_000))

    if total_equity_yen <= 0:
        return None, "invalid_total_equity"

    # ロット
    lot = _lot_size_for_code(code, policy)

    # 1株あたり損失（BUY）
    loss_per_share = e - s
    if loss_per_share <= 0:
        return None, "invalid_loss_per_share"

    # RR
    reward_per_share = t - e
    rr = reward_per_share / loss_per_share if loss_per_share > 0 else None
    if rr is None or rr < min_reward_risk:
        return None, "rr_too_low"

    # リスク許容（円）
    risk_yen = total_equity_yen * (risk_pct / 100.0)

    # 目標数量
    qty_raw = int(risk_yen // loss_per_share)
    if qty_raw <= 0:
        return None, "qty_zero_by_risk"

    # ロット丸め
    qty = (qty_raw // lot) * lot
    if qty <= 0:
        return None, "qty_zero_by_lot"

    # 信用余力上限（ざっくり：必要資金が equity*credit_usage_pct% を超えたら落とす）
    required_cash = e * qty
    max_cash = total_equity_yen * (credit_usage_pct / 100.0)
    if required_cash > max_cash:
        # 可能なら qty を落として収める
        qty2 = int(max_cash // e)
        qty2 = (qty2 // lot) * lot
        if qty2 <= 0:
            return None, "qty_zero_by_credit_limit"
        qty = qty2
        required_cash = e * qty

    # 手数料・スリッページ（往復っぽく見る）
    # ※ここは「プロ仕様」の簡易：往復手数料=2回分、スリッページ=往復1回分（控えめ）
    notional = e * qty
    commission_one = max(min_commission, commission_rate * notional)
    commission_roundtrip = 2.0 * commission_one
    slippage = slippage_rate * notional

    # 想定利益（円）
    gross_profit = (t - e) * qty
    net_profit = gross_profit - commission_roundtrip - slippage

    # 想定損失（円）…マイナスで持つ（あなたの既存仕様に合わせる）
    gross_loss = (s - e) * qty  # negative
    net_loss = gross_loss - commission_roundtrip - slippage  # more negative

    if (not allow_negative_pl) and (net_profit <= 0):
        return None, "negative_net_profit"

    if net_profit < min_net_profit_yen:
        return None, "net_profit_too_low"

    res = ProSizingResult(
        qty_pro=int(qty),
        required_cash_pro=float(required_cash),
        est_pl_pro=float(net_profit),
        est_loss_pro=float(net_loss),
        rr=float(rr) if rr is not None else None,
        net_profit_yen=float(net_profit),
        reason_skip="",
    )
    return res, "ok"