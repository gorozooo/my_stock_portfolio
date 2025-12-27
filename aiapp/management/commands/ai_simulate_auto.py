# aiapp/management/commands/ai_simulate_auto.py
# -*- coding: utf-8 -*-
"""
ai_simulate_auto

紙トレ自動エントリー（DEMO）コマンド。

役割:
- media/aiapp/picks/latest_full.json を読み込む
- TopK の注文を JSONL に起票する（既存パイプライン互換）
- 同時に VirtualTrade(DB) に "OPEN" として同期する（UI/⭐️集計用）

重要:
- run_date は「起票した日（JST）」
- trade_date は「評価の基準日（通常は同日）」

あなたの運用ルール（現実世界と同じ）:
- opened_at（注文を作った時刻）= 現実世界だと注文を出した時間
- ただし、場が終わった後（15:30以降）に作った注文は、現実では翌営業日に執行される
  → --trade-date を省略した場合のみ、trade_date を自動で「次の営業日」に送る

★PRO仕様（今回の本丸）：
- PRO統一口座（資金: settings.AIAPP_PRO_EQUITY_YEN）で qty_pro / required_cash_pro / est_pl_pro / est_loss_pro を計算
- ポリシー（aiapp/policies/short_aggressive.yml）でフィルタ（純利益/ RR など）
- 同時ポジション制限（max_positions / max_total_risk_r）を適用
- ★資金プール制約（本修正）：
  - 口座枠（policy.pro.profiles[mode].limits.max_total_notional_yen）を上限として使う
  - reserve_cash_yen（常に残す）を差し引く
  - 既存OPEN（PRO accepted）で使っている required_cash_pro を差し引いた残高 cash_left を計算
  - 新規 accepted は required_cash_pro が cash_left 以下のものだけ
  - 足りないものは rejected_by_cash（DBには残すが JSONLには出さない）
- ★C: “資金を割って建玉を増やす”：
  - 残り枠（remaining_slots）で cash_left を割り、1銘柄あたりの目安（target_per_trade_yen）を作る
  - 1銘柄上限（max_notional_per_trade_yen）と合わせて cap_yen を確定
  - その cap_yen に収まるように qty_pro を丸め直す（lot単位）
  - min_notional_per_trade_yen が 0 でなければ下限として判定

★A案（詰まり解消）：
- 同時ポジション制限で “OPEN扱い” に数えるのは、PROで accepted になったものだけ
  → 過去の carry（PRO移行前の残骸）は枠を食わない

★B（今回の実装）：
- shape（entry_k/rr_target/tp_k/sl_k）を simulate 側が必ず吐く
  → ml_ok=False でも shape はデフォルト形で数値を埋める（UIが毎日安定）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats

# ★追加：紙シミュ保存の本体化（特徴量 & 距離）
try:
    from aiapp.services.fetch_price import get_prices
except Exception:  # pragma: no cover
    get_prices = None  # type: ignore

try:
    from aiapp.models.features import make_features, FeatureConfig
except Exception:  # pragma: no cover
    make_features = None  # type: ignore
    FeatureConfig = None  # type: ignore

# ★PRO仕様：ポリシー＆口座サイズ＆同時建玉制限
try:
    from aiapp.services.pro_account import load_policy_yaml, compute_pro_sizing_and_filter
except Exception:  # pragma: no cover
    load_policy_yaml = None  # type: ignore
    compute_pro_sizing_and_filter = None  # type: ignore

try:
    from aiapp.services.position_limits import LimitConfig, PositionLimitManager
except Exception:  # pragma: no cover
    LimitConfig = None  # type: ignore
    PositionLimitManager = None  # type: ignore

# ★②ML推論（LightGBM latest）
try:
    from aiapp.services.ml_predict import predict_latest
except Exception:  # pragma: no cover
    predict_latest = None  # type: ignore

# ★B: shape係数（simulate側で必ず吐く）
try:
    from aiapp.services.entry_service import compute_shape_coeffs
except Exception:  # pragma: no cover
    compute_shape_coeffs = None  # type: ignore


# ========= パス定義（MEDIA_ROOT ベース） =========
PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"
SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

# ★NEW: behavior latest（UIが末尾1行を見る）
BEHAVIOR_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
BEHAVIOR_LATEST = BEHAVIOR_DIR / "latest_behavior.jsonl"


# ========= 時刻ユーティリティ（JST固定） =========
def _now_jst():
    from datetime import datetime, timezone as _tz, timedelta
    JST = _tz(timedelta(hours=9))
    return datetime.now(JST)

def dt_now_jst_iso() -> str:
    return _now_jst().isoformat()

def today_jst_str() -> str:
    return _now_jst().date().isoformat()

def dt_now_run_id(prefix: str = "auto") -> str:
    n = _now_jst()
    return n.strftime("%Y%m%d_%H%M%S") + f"_{prefix}"


def _parse_date(s: str):
    from datetime import date as _date
    return _date.fromisoformat(s)

def _parse_dt_iso(ts: str) -> Optional[timezone.datetime]:
    try:
        dt = timezone.datetime.fromisoformat(ts)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _next_weekday(d):
    # JPX祝日はここでは扱わず「最低限：土日だけスキップ」
    # （必要なら後で JPX カレンダー対応を足す）
    from datetime import timedelta
    x = d
    while x.weekday() >= 5:  # 5=Sat, 6=Sun
        x = x + timedelta(days=1)
    return x

def _add_days(d, n: int):
    from datetime import timedelta
    return d + timedelta(days=n)

def _jst_session_bounds_for(d):
    """
    その営業日のザラ場端（JST）
    - 9:00〜15:30
    """
    from datetime import datetime as _dt, time as _time
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 30)), tz)
    return start, end

def _auto_trade_date_str(now_local_dt) -> Tuple[str, str]:
    """
    --trade-date 省略時の trade_date 自動決定
    ルール:
    - 15:30(JST)以降に起票した注文は、現実では翌営業日扱い → trade_date を翌営業日に
    - それ以外は当日

    戻り値: (trade_date_str, reason)
    """
    now_local_dt = timezone.localtime(now_local_dt)
    today = now_local_dt.date()
    _session_start, session_end = _jst_session_bounds_for(today)

    if now_local_dt > session_end:
        cand = _add_days(today, 1)
        cand = _next_weekday(cand)
        return cand.isoformat(), "after_close->next_business_day"
    return today.isoformat(), "same_day"


# ========= 数値ヘルパ =========
def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None

def _safe_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


# ========= lot ルール（policy由来） =========
def _lot_size_from_policy(code: str, policy: Dict[str, Any]) -> int:
    """
    policy.lot_rule を尊重して lot を返す。
    - ETF/ETN prefix なら etf_lot
    - それ以外は stock_lot
    """
    try:
        lr = policy.get("lot_rule") if isinstance(policy.get("lot_rule"), dict) else {}
        prefixes = lr.get("etf_codes_prefix") or ["13", "15"]
        etf_lot = int(lr.get("etf_lot", 1) or 1)
        stock_lot = int(lr.get("stock_lot", 100) or 100)

        s = str(code)
        for p in prefixes:
            if s.startswith(str(p)):
                return max(1, etf_lot)
        return max(1, stock_lot)
    except Exception:
        return 100


# ========= PRO profile 合成 =========
def _get_pro_profile(policy: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    policy.pro.learn_mode と profiles から、現在のプロファイルを返す。
    戻り値: (learn_mode, profile_dict)
    """
    pro = policy.get("pro") if isinstance(policy.get("pro"), dict) else {}
    learn_mode = str(pro.get("learn_mode") or "collect").strip().lower()
    if learn_mode not in ("collect", "strict"):
        learn_mode = "collect"
    profiles = pro.get("profiles") if isinstance(pro.get("profiles"), dict) else {}
    prof = profiles.get(learn_mode) if isinstance(profiles.get(learn_mode), dict) else {}
    return learn_mode, prof


def _merge_policy_for_mode(policy: Dict[str, Any], *, learn_mode: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    learn_mode/profile に合わせて policy を合成する。
    - limits は profile.limits を最優先で policy['limits'] に反映（下流統一）
    - strict のとき tighten を filters に上乗せ（min_reward_risk / min_net_profit_yen）
    """
    merged: Dict[str, Any] = dict(policy or {})

    # limits を profile 側から採用
    plimits = profile.get("limits") if isinstance(profile.get("limits"), dict) else {}
    base_limits = merged.get("limits") if isinstance(merged.get("limits"), dict) else {}
    limits_new = dict(base_limits)
    for k, v in (plimits or {}).items():
        limits_new[k] = v
    merged["limits"] = limits_new

    # strict tighten を filters に反映
    if str(learn_mode) == "strict":
        tighten = profile.get("tighten") if isinstance(profile.get("tighten"), dict) else {}
        filters = merged.get("filters") if isinstance(merged.get("filters"), dict) else {}
        filters_new = dict(filters)

        # tighten: min_reward_risk / min_net_profit_yen（強化）
        if "min_reward_risk" in tighten and tighten.get("min_reward_risk") is not None:
            try:
                filters_new["min_reward_risk"] = float(tighten.get("min_reward_risk"))
            except Exception:
                pass
        if "min_net_profit_yen" in tighten and tighten.get("min_net_profit_yen") is not None:
            try:
                filters_new["min_net_profit_yen"] = float(tighten.get("min_net_profit_yen"))
            except Exception:
                pass

        merged["filters"] = filters_new

    return merged


# =========================================================
# ★NEW: entry_reason（6択のみ。other/未設定は作らない）
# =========================================================
def _pick_entry_reason_from_item(it: Dict[str, Any]) -> str:
    """
    picks の item から entry_reason を決める（その他/未設定は作らない）。
    優先順位：
    1) it["entry_reason"] / it["reason"] / it["setup"] / it["scenario"] の明示値
    2) it["tags"]（list/str）に含まれるキーワード
    3) 最後は trend_follow（フォールバック）
    """
    raw = (
        it.get("entry_reason")
        or it.get("reason")
        or it.get("setup")
        or it.get("scenario")
        or ""
    )

    def norm(s: Any) -> str:
        return str(s or "").strip().lower().replace("-", "_")

    s = norm(raw)

    alias = {
        "trend": "trend_follow",
        "trendfollow": "trend_follow",
        "follow_trend": "trend_follow",
        "順張り": "trend_follow",
        "押し目": "pullback",
        "pull_back": "pullback",
        "break": "breakout",
        "break_out": "breakout",
        "ブレイク": "breakout",
        "逆張り": "reversal",
        "material": "news",
        "材料": "news",
        "range": "mean_revert",
        "レンジ": "mean_revert",
        "meanrevert": "mean_revert",
        "mean_reversion": "mean_revert",
    }
    if s in alias:
        s = alias[s]

    allowed = {
        "trend_follow",
        "pullback",
        "breakout",
        "reversal",
        "news",
        "mean_revert",
    }
    if s in allowed:
        return s

    tags = it.get("tags")
    tlist: List[str] = []
    if isinstance(tags, list):
        tlist = [norm(x) for x in tags]
    elif isinstance(tags, str):
        tlist = [norm(x) for x in tags.split(",")]

    joined = " ".join(tlist)
    if "pullback" in joined or "押し目" in joined:
        return "pullback"
    if "breakout" in joined or "ブレイク" in joined:
        return "breakout"
    if "reversal" in joined or "逆張り" in joined:
        return "reversal"
    if "news" in joined or "材料" in joined:
        return "news"
    if "mean_revert" in joined or "range" in joined or "レンジ" in joined:
        return "mean_revert"
    if "trend_follow" in joined or "trend" in joined or "順張り" in joined:
        return "trend_follow"

    return "trend_follow"


# ========= 本体化：特徴量・距離の保存 =========
def _build_feat_last_and_distance(
    code: str,
    *,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    last_close: Optional[float],
    atr_pick: Optional[float],
) -> Dict[str, Any]:
    """
    VirtualTrade.replay に入れる payload を組み立てる。
    - feat_last: 特徴量スナップショット（安定性評価の材料）
    - distance: 距離妥当性評価の材料（ATR正規化）
    """
    out: Dict[str, Any] = {}

    # 1) 特徴量スナップショット
    feat_last: Optional[Dict[str, Any]] = None
    atr_from_feat: Optional[float] = None

    if get_prices is not None and make_features is not None and FeatureConfig is not None:
        try:
            raw = get_prices(code, nbars=260, period="3y")
            if raw is not None and len(raw) > 0:
                cfg = FeatureConfig()
                feat_df = make_features(raw, cfg=cfg)
                if feat_df is not None and len(feat_df) > 0:
                    row = feat_df.iloc[-1]

                    keep_keys = [
                        f"RSI{getattr(cfg, 'rsi_period', 14)}",
                        "BB_Z",
                        "VWAP_GAP_PCT",
                        "RET_1",
                        "RET_5",
                        "RET_20",
                        f"SLOPE_{getattr(cfg, 'slope_short', 5)}",
                        f"SLOPE_{getattr(cfg, 'slope_mid', 25)}",
                        f"ATR{getattr(cfg, 'atr_period', 14)}",
                        "GCROSS",
                        "DCROSS",
                    ]

                    tmp: Dict[str, Any] = {}
                    for k in keep_keys:
                        if k in row.index:
                            v = row.get(k)
                            if v is None:
                                tmp[k] = None
                            else:
                                try:
                                    fv = float(v)
                                    if fv != fv:
                                        tmp[k] = None
                                    else:
                                        if k in ("GCROSS", "DCROSS"):
                                            tmp[k] = int(fv)
                                        else:
                                            tmp[k] = fv
                                except Exception:
                                    try:
                                        tmp[k] = int(v)
                                    except Exception:
                                        tmp[k] = str(v)

                    def pick_float(key: str) -> Optional[float]:
                        return _safe_float(tmp.get(key))

                    atr_key = f"ATR{getattr(cfg, 'atr_period', 14)}"
                    atr_from_feat = pick_float(atr_key)

                    feat_last = {
                        "RSI14": pick_float(f"RSI{getattr(cfg, 'rsi_period', 14)}"),
                        "BB_Z": pick_float("BB_Z"),
                        "VWAP_GAP_PCT": pick_float("VWAP_GAP_PCT"),
                        "RET_1": pick_float("RET_1"),
                        "RET_5": pick_float("RET_5"),
                        "RET_20": pick_float("RET_20"),
                        "SLOPE_5": pick_float(f"SLOPE_{getattr(cfg, 'slope_short', 5)}"),
                        "SLOPE_25": pick_float(f"SLOPE_{getattr(cfg, 'slope_mid', 25)}"),
                        "ATR14": atr_from_feat,
                        "GCROSS": tmp.get("GCROSS"),
                        "DCROSS": tmp.get("DCROSS"),
                        "raw": tmp,
                    }
        except Exception:
            feat_last = None
            atr_from_feat = None

    if feat_last is not None:
        out["feat_last"] = feat_last

    # 2) 距離妥当性（ATR正規化）
    atr = atr_from_feat if atr_from_feat is not None else atr_pick
    atr = _safe_float(atr)

    e = _safe_float(entry)
    t = _safe_float(tp)
    s = _safe_float(sl)
    lc = _safe_float(last_close)

    dist: Dict[str, Any] = {
        "entry": e,
        "tp": t,
        "sl": s,
        "last_close": lc,
        "atr": atr,
    }

    if e is not None and t is not None and s is not None and atr is not None and atr > 0:
        dist_tp_atr = (t - e) / atr
        dist_sl_atr = (e - s) / atr
        rr = None
        if dist_sl_atr is not None and dist_sl_atr > 0:
            rr = dist_tp_atr / dist_sl_atr

        def clamp(x: Optional[float], lo: float, hi: float) -> Optional[float]:
            if x is None or x != x:
                return None
            if x < lo:
                return lo
            if x > hi:
                return hi
            return x

        dist["dist_tp_atr"] = clamp(_safe_float(dist_tp_atr), -50.0, 50.0)
        dist["dist_sl_atr"] = clamp(_safe_float(dist_sl_atr), -50.0, 50.0)
        dist["rr"] = clamp(_safe_float(rr), -50.0, 50.0)
    else:
        dist["dist_tp_atr"] = None
        dist["dist_sl_atr"] = None
        dist["rr"] = None

    out["distance"] = dist
    return out


# ========= PRO: EV_true_pro（BehaviorStats all/all の win_rate を0-1化） =========
def _ev_true_from_behavior(code: str) -> float:
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period="all", mode_aggr="all")
        .values("win_rate")
        .first()
    )
    if not row:
        return 0.0
    wr = _safe_float(row.get("win_rate"))
    if wr is None:
        return 0.0
    v = max(0.0, min(1.0, wr / 100.0))
    return float(v)


# ========= PRO: policy path =========
def _policy_path_default() -> Path:
    """
    デフォルトのポリシーパスを返す。

    対応方針：
    - 真実ソースは short_aggressive.runtime.yml（Git管理外）
    - runtime が無ければ policy_loader 側でテンプレから自動生成される前提
    - もし policy_loader が使えない状況でも落とさず .yml にフォールバック
    """
    # まず runtime を正とする（policy_loader があれば確実に作る）
    try:
        from aiapp.services.policy_loader import ensure_runtime_policy  # ★追加
        runtime_path = ensure_runtime_policy("short_aggressive")
        return Path(runtime_path)
    except Exception:
        pass

    # フォールバック：従来通り .yml
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        return Path(base_dir) / "aiapp" / "policies" / "short_aggressive.yml"

    return Path(__file__).resolve().parents[4] / "aiapp" / "policies" / "short_aggressive.yml"


# ========= PRO: open positions snapshot =========
def _load_open_positions_for_limits(user) -> Tuple[Dict[str, Dict[str, Any]], float, float]:
    """
    同時ポジション制限に使う “現在オープン扱い” を作る。

    ★A案（PRO移行後の詰まりを解消）:
    - closed_at is None
    - replay.pro.status == "accepted" だけを OPEN扱いにする
      → 過去の carry 残骸（PROでacceptedではないもの）は枠を食わない

    さらに：
    - eval_exit_reason in ("carry", "") を主にオープン扱い
    - eval_entry_px が入っているものを優先（entry済み）

    ★本修正：
    - OPEN(accepted) が占有している required_cash_pro を合算して used_cash_yen を返す
      → 新規runの cash_left 初期値に使う
    """
    qs = (
        VirtualTrade.objects
        .filter(user=user, closed_at=None)
        .filter(replay__pro__status="accepted")
        .filter(Q(eval_exit_reason="carry") | Q(eval_exit_reason=""))
        .exclude(eval_entry_px=None)
        .only("code", "eval_exit_reason", "eval_entry_px", "trade_date", "opened_at", "replay", "required_cash_pro")
    )

    positions: Dict[str, Dict[str, Any]] = {}
    used_cash_yen = 0.0

    for v in qs:
        positions[str(v.code)] = {
            "trade_date": str(v.trade_date),
            "opened_at": str(v.opened_at),
            "eval_entry_px": v.eval_entry_px,
            "risk_r": 1.0,  # 現状設計：1トレード=1R固定
        }
        used_cash_yen += float(v.required_cash_pro or 0.0)

    total_risk = float(len(positions)) * 1.0
    return positions, total_risk, float(used_cash_yen)


def _apply_per_trade_cap_to_pro_res(
    *,
    code: str,
    policy: Dict[str, Any],
    entry: Optional[float],
    pro_res: Any,
    cap_yen: float,
    min_yen: float,
) -> Tuple[Any, str]:
    """
    ★Cの中枢：
    - cap_yen に収まるように qty_pro を lot 単位で丸め直す
    - min_yen（0で無効）未満になるなら reject に倒せるよう reason を返す

    戻り値: (pro_res(書き換え済), cap_reason)
    cap_reason は "" なら変更なし
    """
    e = _safe_float(entry)
    if e is None or e <= 0:
        return pro_res, ""

    try:
        qty0 = int(getattr(pro_res, "qty_pro", 0) or 0)
    except Exception:
        qty0 = 0

    if qty0 <= 0:
        return pro_res, ""

    lot = _lot_size_from_policy(str(code), policy)
    lot = max(1, int(lot))

    # cap が 0/負なら「資金制約でreject」
    if cap_yen <= 0:
        return pro_res, "cap_zero"

    # cap から入れる最大株数（lot切り捨て）
    max_qty_by_cap = int((cap_yen / e) // lot * lot)
    if max_qty_by_cap <= 0:
        return pro_res, "cap_too_small_for_lot"

    qty1 = min(qty0, max_qty_by_cap)
    qty1 = int(qty1 // lot * lot)
    if qty1 <= 0:
        return pro_res, "cap_round_to_zero"

    # min_notional（0で無効）
    if min_yen and min_yen > 0:
        if (e * qty1) < float(min_yen):
            return pro_res, "below_min_notional"

    if qty1 == qty0:
        return pro_res, ""

    # required_cash_pro は厳密に entry*qty とする（安全側）
    req1 = float(e * qty1)

    # est_pl_pro / est_loss_pro は数量比例で近似
    try:
        pl0 = float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0)
    except Exception:
        pl0 = 0.0
    try:
        loss0 = float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0)
    except Exception:
        loss0 = 0.0

    ratio = (float(qty1) / float(qty0)) if qty0 > 0 else 1.0
    pl1 = pl0 * ratio
    loss1 = loss0 * ratio

    try:
        setattr(pro_res, "qty_pro", int(qty1))
        setattr(pro_res, "required_cash_pro", float(req1))
        setattr(pro_res, "est_pl_pro", float(pl1))
        setattr(pro_res, "est_loss_pro", float(loss1))
    except Exception:
        return pro_res, ""

    return pro_res, f"cap_applied({qty0}->{qty1}, cap={cap_yen:.0f})"


# =========================================================
# ★NEW: defaults 共通生成（分岐のコピペ地獄を減らす）
# =========================================================
def _make_vtrade_defaults_base(
    *,
    run_date,
    trade_date,
    source: str,
    mode: str,
    code: str,
    name: str,
    sector: str,
    side: str,
    universe: str,
    style: str,
    horizon: str,
    topk: Any,
    score: Any,
    score_100: Any,
    stars: Any,
    mode_period: str,
    mode_aggr: str,
    entry: Any,
    tp: Any,
    sl: Any,
    last_close: Any,
    opened_at_dt,
    entry_reason: str,
    ev_true_pro: float,
) -> Dict[str, Any]:
    return dict(
        run_date=run_date,
        trade_date=trade_date,
        source=source,
        mode=mode,
        code=str(code),
        name=str(name or ""),
        sector=str(sector or ""),
        side=str(side or "BUY"),
        universe=str(universe or ""),
        style=str(style or ""),
        horizon=str(horizon or ""),
        topk=topk if isinstance(topk, int) else _safe_int(topk),
        score=score if score is None else float(score),
        score_100=score_100 if score_100 is None else int(score_100),
        stars=stars if stars is None else int(stars),
        mode_period=str(mode_period),
        mode_aggr=str(mode_aggr),
        entry_px=entry if entry is None else float(entry),
        tp_px=tp if tp is None else float(tp),
        sl_px=sl if sl is None else float(sl),
        last_close=last_close if last_close is None else float(last_close),
        opened_at=opened_at_dt,
        entry_reason=str(entry_reason),
        ev_true_pro=float(ev_true_pro),
    )


# =========================================================
# ★NEW: behavior/latest_behavior.jsonl へ追記（UIが末尾1行を見る）
# =========================================================
def _append_latest_behavior_jsonl(row: Dict[str, Any]) -> None:
    """
    media/aiapp/behavior/latest_behavior.jsonl に 1行JSON を追記する。
    - 既存コマンド(build_behavior_dataset 等)が作る形式に「寄せる」ため、
      UIで参照されるキーは必ずトップレベルに置く（entry_k/rr_target/tp_k/sl_k 等）。
    """
    try:
        BEHAVIOR_DIR.mkdir(parents=True, exist_ok=True)
        with BEHAVIOR_LATEST.open("a", encoding="utf-8") as fw:
            fw.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        # ここで落ちるとシミュレ全体が止まるので黙って握る（ログはDB側に残る）
        return


def _make_behavior_row_for_ui(
    *,
    code: str,
    ts_iso: str,
    sim_order: Optional[Dict[str, Any]],
    replay: Optional[Dict[str, Any]],
    ml_ok: bool,
    p_win: Any,
    ev_pred: Any,
    p_tp_first: Any,
    p_sl_first: Any,
    ev_true: float,
    shape_entry_k: Any,
    shape_rr_target: Any,
    shape_tp_k: Any,
    shape_sl_k: Any,
) -> Dict[str, Any]:
    """
    UIが末尾1行だけ読んでも安定して表示できるように、必要キーをトップレベルに整形する。
    """
    def f3(x):
        v = _safe_float(x)
        return float(v) if v is not None else None

    return {
        "code": str(code),
        "ts": str(ts_iso),

        # ML
        "ml_ok": bool(ml_ok),
        "p_win": f3(p_win) if ml_ok else None,
        "ev_pred": f3(ev_pred) if ml_ok else None,
        "p_tp_first": f3(p_tp_first) if ml_ok else None,
        "p_sl_first": f3(p_sl_first) if ml_ok else None,
        "ev_true": float(ev_true),

        # shape（★B：simulate側が必ず吐く本体）
        "entry_k": f3(shape_entry_k),
        "rr_target": f3(shape_rr_target),
        "tp_k": f3(shape_tp_k),
        "sl_k": f3(shape_sl_k),

        # 互換キー（UI/調査用）
        "shape_entry_k": f3(shape_entry_k),
        "shape_rr_target": f3(shape_rr_target),
        "shape_tp_k": f3(shape_tp_k),
        "shape_sl_k": f3(shape_sl_k),

        # 原本（調査用）
        "sim_order": sim_order if isinstance(sim_order, dict) else None,
        "replay": replay if isinstance(replay, dict) else None,
    }


class Command(BaseCommand):
    help = "AIフル自動シミュレ用：DEMO紙トレ注文を JSONL に起票 + VirtualTrade同期（PRO仕様）"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="run_date: YYYY-MM-DD（指定がなければJSTの今日）")
        parser.add_argument(
            "--trade-date",
            type=str,
            default=None,
            help="trade_date: YYYY-MM-DD（省略時は自動決定。基本は同日、15:30以降は翌営業日）",
        )
        parser.add_argument("--overwrite", action="store_true", help="同じ日付の jsonl を上書き")
        parser.add_argument("--mode-period", type=str, default="short", help="short/mid/long（将来拡張）")
        parser.add_argument("--mode-aggr", type=str, default="aggr", help="aggr/norm/def（将来拡張）")
        parser.add_argument(
            "--policy",
            type=str,
            default=None,
            help="policy yml path（省略時: aiapp/policies/short_aggressive.runtime.yml を優先。無ければ short_aggressive.yml）",
        )
        parser.add_argument("--dry-run", action="store_true", help="DB/JSONLを書かずにログ（確認用）")

    def handle(self, *args, **options):
        run_date_str: str = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))
        dry_run: bool = bool(options.get("dry_run"))

        mode_period: str = (options.get("mode_period") or "short").strip().lower()
        mode_aggr: str = (options.get("mode_aggr") or "aggr").strip().lower()

        # ---------- policy ----------
        policy_path = Path(options.get("policy") or _policy_path_default())
        if load_policy_yaml is None or compute_pro_sizing_and_filter is None:
            self.stdout.write(self.style.ERROR("[ai_simulate_auto] pro_account is not available (import failed)"))
            return
        if LimitConfig is None or PositionLimitManager is None:
            self.stdout.write(self.style.ERROR("[ai_simulate_auto] position_limits is not available (import failed)"))
            return
        try:
            policy_raw = load_policy_yaml(str(policy_path))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ai_simulate_auto] policy load error: {e} path={policy_path}"))
            return

        # ---------- PRO profile (C) ----------
        learn_mode, profile = _get_pro_profile(policy_raw)
        policy = _merge_policy_for_mode(policy_raw, learn_mode=learn_mode, profile=profile)

        # profile limits（Cの資金配分に使う）
        prof_limits = profile.get("limits") if isinstance(profile.get("limits"), dict) else {}
        max_notional_per_trade_yen = float(prof_limits.get("max_notional_per_trade_yen", 0) or 0)
        min_notional_per_trade_yen = float(prof_limits.get("min_notional_per_trade_yen", 0) or 0)
        max_total_notional_yen = float(prof_limits.get("max_total_notional_yen", 0) or 0)
        reserve_cash_yen = float(prof_limits.get("reserve_cash_yen", 0) or 0)

        # ---------- picks load ----------
        picks_path = PICKS_DIR / "latest_full.json"
        if not picks_path.exists():
            self.stdout.write(self.style.WARNING(f"[ai_simulate_auto] picks not found: {picks_path}"))
            return

        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ai_simulate_auto] picks load error: {e}"))
            return

        meta: Dict[str, Any] = data.get("meta") or {}
        items: List[Dict[str, Any]] = data.get("items") or []
        if not items:
            self.stdout.write(self.style.WARNING("[ai_simulate_auto] items=0"))
            return

        # ---------- user ----------
        User = get_user_model()
        user = User.objects.order_by("id").first()
        if not user:
            self.stdout.write(self.style.ERROR("[ai_simulate_auto] no user found"))
            return
        user_id = user.id

        # ---------- run meta ----------
        style = (meta.get("style") or "aggressive")
        horizon = (meta.get("horizon") or "short")
        universe = (meta.get("universe") or "unknown")
        topk = meta.get("topk")

        run_id = dt_now_run_id(prefix="auto_demo_pro")

        SIM_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SIM_DIR / f"sim_orders_{run_date_str}.jsonl"
        file_mode = "w" if overwrite else "a"

        ts_iso = dt_now_jst_iso()
        opened_at_dt = _parse_dt_iso(ts_iso) or timezone.now()  # JST aware（DB保存はUTC）

        # run_date / trade_date
        run_date = _parse_date(run_date_str)
        trade_date_opt: Optional[str] = options.get("trade_date")
        if trade_date_opt:
            trade_date_str = trade_date_opt
            trade_date_reason = "explicit"
        else:
            trade_date_str, trade_date_reason = _auto_trade_date_str(opened_at_dt)
        trade_date = _parse_date(trade_date_str)

        # ---------- PRO equity ----------
        total_equity_yen = float(getattr(settings, "AIAPP_PRO_EQUITY_YEN", 3_000_000) or 3_000_000)

        # ---------- position limits（policy['limits'] は profile 合成済み） ----------
        limits_cfg = policy.get("limits") if isinstance(policy.get("limits"), dict) else {}
        max_positions = int(limits_cfg.get("max_positions", 5) or 5)
        max_total_risk_r = float(limits_cfg.get("max_total_risk_r", 3.0) or 3.0)

        mgr = PositionLimitManager(LimitConfig(max_positions=max_positions, max_total_risk_r=max_total_risk_r))
        open_positions, total_risk_r, used_cash_yen = _load_open_positions_for_limits(user)
        mgr.load_open_positions(open_positions, total_risk_r=total_risk_r)

        # ---------- cash pool (C: 口座枠 / 予備資金 / 既存占有) ----------
        total_notional_cap = max_total_notional_yen if max_total_notional_yen > 0 else float(total_equity_yen)

        cash_start = float(total_notional_cap)
        cash_used_before = float(used_cash_yen)
        cash_left = cash_start - float(reserve_cash_yen) - cash_used_before
        if cash_left < 0:
            cash_left = 0.0

        # ---------- candidates: EV_true_pro desc ----------
        cands: List[Dict[str, Any]] = []
        for it in items:
            code = (it.get("code") or "").strip()
            if not code:
                continue
            ev = _ev_true_from_behavior(code)
            it2 = dict(it)
            it2["_ev_true_pro"] = ev
            cands.append(it2)

        cands.sort(key=lambda x: float(x.get("_ev_true_pro", 0.0) or 0.0), reverse=True)

        written = 0
        upserted = 0
        accepted = 0
        rejected_by_cash = 0
        skipped_pro_filter = 0
        skipped_limits = 0

        # ★NEW: behavior latest に書く候補（accepted優先）
        last_behavior_any: Optional[Dict[str, Any]] = None
        last_behavior_accepted: Optional[Dict[str, Any]] = None

        # ---------- header log ----------
        if dry_run:
            self.stdout.write(
                f"[ai_simulate_auto] DRY-RUN run_id={run_id} run_date={run_date_str} trade_date={trade_date_str}({trade_date_reason}) "
                f"policy={policy_path} pro_mode={learn_mode} "
                f"cap_total={cash_start:.0f} reserve={float(reserve_cash_yen):.0f} used_before={cash_used_before:.0f} cash_left={cash_left:.0f} "
                f"limits=(pos={max_positions}, risk={max_total_risk_r:.2f}R) open_now={mgr.count_open()} total_risk={mgr.total_risk_r:.2f} "
                f"items={len(cands)}"
            )

        fw = None
        try:
            if not dry_run:
                fw = out_path.open(file_mode, encoding="utf-8")

            for it in cands:
                code = (it.get("code") or "").strip()
                if not code:
                    continue

                name = it.get("name")
                sector = it.get("sector_display")

                side = "BUY"
                rec_mode = "demo"

                entry = it.get("entry", it.get("last_close"))
                tp = it.get("tp")
                sl = it.get("sl")
                last_close = it.get("last_close")
                atr_pick = it.get("atr")

                score = it.get("score")
                score_100 = it.get("score_100")
                stars = it.get("stars")

                # ★NEW: entry_reason（6択に正規化）
                entry_reason = _pick_entry_reason_from_item(it)

                # 共通で保存したい payload
                payload_extra = _build_feat_last_and_distance(
                    code=code,
                    entry=_safe_float(entry),
                    tp=_safe_float(tp),
                    sl=_safe_float(sl),
                    last_close=_safe_float(last_close),
                    atr_pick=_safe_float(atr_pick),
                )

                # ---------- ★② ML predict（latest） ----------
                ml_ok = False
                ml_reason = "ml_not_available"
                p_win = None
                ev_pred = None
                p_tp_first = None
                p_sl_first = None

                if predict_latest is not None:
                    try:
                        feat_last = payload_extra.get("feat_last") if isinstance(payload_extra, dict) else None
                        res = predict_latest(
                            feat_last=feat_last if isinstance(feat_last, dict) else None,
                            score_100=score_100,
                            entry=entry,
                            tp=tp,
                            sl=sl,
                        )
                        ml_ok = bool(getattr(res, "ok", False))
                        ml_reason = str(getattr(res, "reason", ""))
                        if ml_ok:
                            p_win = getattr(res, "p_win", None)
                            ev_pred = getattr(res, "ev_pred", None)
                            p_tp_first = getattr(res, "p_tp_first", None)
                            p_sl_first = getattr(res, "p_sl_first", None)
                    except Exception as e:
                        ml_ok = False
                        ml_reason = f"ml_exception({type(e).__name__})"

                # =========================================================
                # ★B: shape を simulate 側で必ず吐く（ml_ok=Falseでも）
                # =========================================================
                shape_entry_k = None
                shape_rr_target = None
                shape_tp_k = None
                shape_sl_k = None

                if compute_shape_coeffs is not None:
                    try:
                        # last/atr は「形」用に last_close を優先（無ければ entry からでも）
                        last_for_shape = _safe_float(last_close)
                        if last_for_shape is None:
                            last_for_shape = _safe_float(entry)
                        atr_for_shape = _safe_float(atr_pick)

                        # B仕様：ml_ok=Falseなら p_tp_first を渡さない（= None）
                        p_for_shape = _safe_float(p_tp_first) if ml_ok else None

                        # mode/horizon は meta に合わせる（style は aggressive 等）
                        sh = compute_shape_coeffs(
                            last=float(last_for_shape) if last_for_shape is not None else 0.0,
                            atr=float(atr_for_shape) if atr_for_shape is not None else 0.0,
                            mode=str(style or "aggressive"),
                            horizon=str(horizon or "short"),
                            p_tp_first=p_for_shape,
                        )
                        if isinstance(sh, dict):
                            shape_entry_k = sh.get("entry_k")
                            shape_rr_target = sh.get("rr_target")
                            shape_tp_k = sh.get("tp_k")
                            shape_sl_k = sh.get("sl_k")
                    except Exception:
                        shape_entry_k = None
                        shape_rr_target = None
                        shape_tp_k = None
                        shape_sl_k = None

                # ここで使う run 共通メタ（各レコードに入れて監査できるようにする）
                run_common_pro_meta = {
                    "policy": str(policy_path),
                    "pro_mode": str(learn_mode),
                    "equity_yen": float(total_equity_yen),              # 資産側（参考）
                    "notional_cap_yen": float(cash_start),              # 口座枠（Cの真）
                    "reserve_cash_yen": float(reserve_cash_yen),
                    "used_before_yen": float(cash_used_before),
                    "trade_date_reason": str(trade_date_reason),
                    "run_id": str(run_id),

                    # ★NEW: reason監査
                    "entry_reason": str(entry_reason),
                    "entry_reason_src": str(
                        "explicit"
                        if (it.get("entry_reason") or it.get("reason") or it.get("setup") or it.get("scenario"))
                        else ("tags" if it.get("tags") else "default")
                    ),

                    # ★B: shape 監査（常に埋める想定）
                    "shape": {
                        "entry_k": (float(shape_entry_k) if shape_entry_k is not None else None),
                        "rr_target": (float(shape_rr_target) if shape_rr_target is not None else None),
                        "tp_k": (float(shape_tp_k) if shape_tp_k is not None else None),
                        "sl_k": (float(shape_sl_k) if shape_sl_k is not None else None),
                        "src": "entry_service.compute_shape_coeffs" if compute_shape_coeffs is not None else "not_available",
                        "p_tp_first_used": (float(_safe_float(p_tp_first)) if (ml_ok and _safe_float(p_tp_first) is not None) else None),
                    },
                }

                # ---------- PRO sizing + filters（合成済 policy を渡す） ----------
                pro_res = None
                pro_reason = ""
                try:
                    pro_res, pro_reason = compute_pro_sizing_and_filter(
                        code=str(code),
                        side=str(side),
                        entry=_safe_float(entry),
                        tp=_safe_float(tp),
                        sl=_safe_float(sl),
                        policy=policy,
                        total_equity_yen=float(total_equity_yen),
                    )
                except Exception as e:
                    pro_res = None
                    pro_reason = f"pro_exception({type(e).__name__})"

                # base sim order（どの分岐でも replay に残す）
                sim_order_base: Dict[str, Any] = {
                    "user_id": user_id,
                    "mode": rec_mode,
                    "ts": ts_iso,
                    "run_date": run_date_str,
                    "trade_date": trade_date_str,
                    "run_id": run_id,
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "side": side,
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "last_close": last_close,
                    "atr": atr_pick,
                    "score": score,
                    "score_100": score_100,
                    "stars": stars,
                    "style": style,
                    "horizon": horizon,
                    "universe": universe,
                    "topk": topk,
                    "source": "ai_simulate_auto",

                    # ★NEW
                    "entry_reason": entry_reason,

                    # ★② ML（監査用：全分岐で持つ）
                    "ml_ok": bool(ml_ok),
                    "ml_reason": str(ml_reason),
                    "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                    "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                    "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                    "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                    "ev_true": float(_ev_true_from_behavior(code)),

                    # ★B: shape（simulate側が吐く本体）
                    "entry_k": (float(shape_entry_k) if shape_entry_k is not None else None),
                    "rr_target": (float(shape_rr_target) if shape_rr_target is not None else None),
                    "tp_k": (float(shape_tp_k) if shape_tp_k is not None else None),
                    "sl_k": (float(shape_sl_k) if shape_sl_k is not None else None),

                    # ★互換キー（UI/調査用に “shape_*” も同値で入れる）
                    "shape_entry_k": (float(shape_entry_k) if shape_entry_k is not None else None),
                    "shape_rr_target": (float(shape_rr_target) if shape_rr_target is not None else None),
                    "shape_tp_k": (float(shape_tp_k) if shape_tp_k is not None else None),
                    "shape_sl_k": (float(shape_sl_k) if shape_sl_k is not None else None),
                }

                if pro_res is None:
                    skipped_pro_filter += 1

                    defaults = _make_vtrade_defaults_base(
                        run_date=run_date,
                        trade_date=trade_date,
                        source="ai_simulate_auto",
                        mode=rec_mode,
                        code=code,
                        name=name or "",
                        sector=sector or "",
                        side=side,
                        universe=str(universe or ""),
                        style=str(style or ""),
                        horizon=str(horizon or ""),
                        topk=topk if isinstance(topk, int) else _safe_int(topk),
                        score=score if score is None else float(score),
                        score_100=score_100 if score_100 is None else int(score_100),
                        stars=stars if stars is None else int(stars),
                        mode_period=mode_period,
                        mode_aggr=mode_aggr,
                        entry=entry if entry is None else float(entry),
                        tp=tp if tp is None else float(tp),
                        sl=sl if sl is None else float(sl),
                        last_close=last_close if last_close is None else float(last_close),
                        opened_at_dt=opened_at_dt,
                        entry_reason=entry_reason,
                        ev_true_pro=_ev_true_from_behavior(code),
                    )
                    defaults["replay"] = {
                        "sim_order": sim_order_base,
                        "opened_at_local": str(timezone.localtime(opened_at_dt)),
                        "pro": {
                            **run_common_pro_meta,
                            "status": "skipped_by_pro_filter",
                            "reason": str(pro_reason),

                            # ★② ML監査
                            "ml": {
                                "ok": bool(ml_ok),
                                "reason": str(ml_reason),
                                "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                                "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                                "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                                "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                                "ev_true": float(_ev_true_from_behavior(code)),
                            },
                        },
                        **payload_extra,
                    }

                    # ★NEW: behavior候補（acceptedが無いrunの保険）
                    last_behavior_any = _make_behavior_row_for_ui(
                        code=code,
                        ts_iso=ts_iso,
                        sim_order=sim_order_base,
                        replay=defaults.get("replay"),
                        ml_ok=ml_ok,
                        p_win=p_win,
                        ev_pred=ev_pred,
                        p_tp_first=p_tp_first,
                        p_sl_first=p_sl_first,
                        ev_true=float(_ev_true_from_behavior(code)),
                        shape_entry_k=shape_entry_k,
                        shape_rr_target=shape_rr_target,
                        shape_tp_k=shape_tp_k,
                        shape_sl_k=shape_sl_k,
                    )

                    if not dry_run:
                        VirtualTrade.objects.update_or_create(
                            user=user,
                            run_id=run_id,
                            code=code,
                            defaults=defaults,
                        )
                        upserted += 1
                    continue

                # ---------- C: 残り枠で資金を割って cap を作る ----------
                open_now = mgr.count_open()
                remaining_slots = max(1, int(max_positions) - int(open_now))
                target_per_trade_yen = float(cash_left) / float(remaining_slots) if remaining_slots > 0 else float(cash_left)

                cap_yen = float(target_per_trade_yen)
                if max_notional_per_trade_yen and max_notional_per_trade_yen > 0:
                    cap_yen = min(cap_yen, float(max_notional_per_trade_yen))

                min_yen = float(min_notional_per_trade_yen or 0.0)

                pro_res, cap_reason = _apply_per_trade_cap_to_pro_res(
                    code=str(code),
                    policy=policy,
                    entry=_safe_float(entry),
                    pro_res=pro_res,
                    cap_yen=cap_yen,
                    min_yen=min_yen,
                )

                # cap により reject
                if cap_reason in ("cap_zero", "cap_too_small_for_lot", "cap_round_to_zero", "below_min_notional"):
                    rejected_by_cash += 1

                    try:
                        req_cash_bad = float(getattr(pro_res, "required_cash_pro", 0.0) or 0.0)
                    except Exception:
                        req_cash_bad = 0.0

                    defaults = _make_vtrade_defaults_base(
                        run_date=run_date,
                        trade_date=trade_date,
                        source="ai_simulate_auto",
                        mode=rec_mode,
                        code=code,
                        name=name or "",
                        sector=sector or "",
                        side=side,
                        universe=str(universe or ""),
                        style=str(style or ""),
                        horizon=str(horizon or ""),
                        topk=topk if isinstance(topk, int) else _safe_int(topk),
                        score=score if score is None else float(score),
                        score_100=score_100 if score_100 is None else int(score_100),
                        stars=stars if stars is None else int(stars),
                        mode_period=mode_period,
                        mode_aggr=mode_aggr,
                        entry=entry if entry is None else float(entry),
                        tp=tp if tp is None else float(tp),
                        sl=sl if sl is None else float(sl),
                        last_close=last_close if last_close is None else float(last_close),
                        opened_at_dt=opened_at_dt,
                        entry_reason=entry_reason,
                        ev_true_pro=_ev_true_from_behavior(code),
                    )
                    defaults.update(
                        qty_pro=int(getattr(pro_res, "qty_pro", 0) or 0),
                        required_cash_pro=float(req_cash_bad),
                        est_pl_pro=float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                        est_loss_pro=float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                    )
                    defaults["replay"] = {
                        "sim_order": sim_order_base,
                        "opened_at_local": str(timezone.localtime(opened_at_dt)),
                        "pro": {
                            **run_common_pro_meta,
                            "status": "rejected_by_cash",
                            "reason": f"cap_reject:{cap_reason}",

                            # ★② ML監査
                            "ml": {
                                "ok": bool(ml_ok),
                                "reason": str(ml_reason),
                                "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                                "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                                "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                                "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                                "ev_true": float(_ev_true_from_behavior(code)),
                            },

                            "cap": {
                                "remaining_slots": int(remaining_slots),
                                "target_per_trade_yen": float(target_per_trade_yen),
                                "cap_yen": float(cap_yen),
                                "min_yen": float(min_yen),
                            },
                            "cash": {
                                "cash_before": float(cash_left),
                                "required_cash_pro": float(req_cash_bad),
                                "cash_after": float(cash_left),
                            },
                            "sizing": {
                                "qty_pro": int(getattr(pro_res, "qty_pro", 0) or 0),
                                "required_cash_pro": float(req_cash_bad),
                                "est_pl_pro": float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                                "est_loss_pro": float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                                "rr": float(getattr(pro_res, "rr", 0.0) or 0.0) if getattr(pro_res, "rr", None) is not None else None,
                                "net_profit_yen": float(getattr(pro_res, "net_profit_yen", 0.0) or 0.0) if getattr(pro_res, "net_profit_yen", None) is not None else None,
                            },
                        },
                        **payload_extra,
                    }

                    # ★NEW: behavior候補（acceptedが無いrunの保険）
                    last_behavior_any = _make_behavior_row_for_ui(
                        code=code,
                        ts_iso=ts_iso,
                        sim_order=sim_order_base,
                        replay=defaults.get("replay"),
                        ml_ok=ml_ok,
                        p_win=p_win,
                        ev_pred=ev_pred,
                        p_tp_first=p_tp_first,
                        p_sl_first=p_sl_first,
                        ev_true=float(_ev_true_from_behavior(code)),
                        shape_entry_k=shape_entry_k,
                        shape_rr_target=shape_rr_target,
                        shape_tp_k=shape_tp_k,
                        shape_sl_k=shape_sl_k,
                    )

                    if dry_run:
                        self.stdout.write(
                            f"  reject_cap code={code} cap_reason={cap_reason} cap={cap_yen:.0f} cash_left={cash_left:.0f} entry_reason={entry_reason}"
                        )
                        continue

                    VirtualTrade.objects.update_or_create(
                        user=user,
                        run_id=run_id,
                        code=code,
                        defaults=defaults,
                    )
                    upserted += 1
                    continue

                # ---------- cash pool constraint ----------
                try:
                    req_cash = float(getattr(pro_res, "required_cash_pro", 0.0) or 0.0)
                except Exception:
                    req_cash = 0.0
                cash_before = float(cash_left)

                if req_cash <= 0 or req_cash > cash_left:
                    rejected_by_cash += 1
                    reason = "bad_required_cash_pro" if req_cash <= 0 else "insufficient_cash"

                    defaults = _make_vtrade_defaults_base(
                        run_date=run_date,
                        trade_date=trade_date,
                        source="ai_simulate_auto",
                        mode=rec_mode,
                        code=code,
                        name=name or "",
                        sector=sector or "",
                        side=side,
                        universe=str(universe or ""),
                        style=str(style or ""),
                        horizon=str(horizon or ""),
                        topk=topk if isinstance(topk, int) else _safe_int(topk),
                        score=score if score is None else float(score),
                        score_100=score_100 if score_100 is None else int(score_100),
                        stars=stars if stars is None else int(stars),
                        mode_period=mode_period,
                        mode_aggr=mode_aggr,
                        entry=entry if entry is None else float(entry),
                        tp=tp if tp is None else float(tp),
                        sl=sl if sl is None else float(sl),
                        last_close=last_close if last_close is None else float(last_close),
                        opened_at_dt=opened_at_dt,
                        entry_reason=entry_reason,
                        ev_true_pro=_ev_true_from_behavior(code),
                    )
                    defaults.update(
                        qty_pro=int(getattr(pro_res, "qty_pro", 0) or 0),
                        required_cash_pro=float(req_cash),
                        est_pl_pro=float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                        est_loss_pro=float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                    )
                    defaults["replay"] = {
                        "sim_order": sim_order_base,
                        "opened_at_local": str(timezone.localtime(opened_at_dt)),
                        "pro": {
                            **run_common_pro_meta,
                            "status": "rejected_by_cash",
                            "reason": reason,

                            # ★② ML監査
                            "ml": {
                                "ok": bool(ml_ok),
                                "reason": str(ml_reason),
                                "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                                "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                                "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                                "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                                "ev_true": float(_ev_true_from_behavior(code)),
                            },

                            "cap": {
                                "remaining_slots": int(max(1, max_positions - mgr.count_open())),
                                "target_per_trade_yen": float(target_per_trade_yen),
                                "cap_yen": float(cap_yen),
                                "min_yen": float(min_yen),
                                "cap_reason": str(cap_reason or ""),
                            },
                            "cash": {
                                "cash_before": cash_before,
                                "required_cash_pro": req_cash,
                                "cash_after": cash_before,
                            },
                            "sizing": {
                                "qty_pro": int(getattr(pro_res, "qty_pro", 0) or 0),
                                "required_cash_pro": req_cash,
                                "est_pl_pro": float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                                "est_loss_pro": float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                                "rr": float(getattr(pro_res, "rr", 0.0) or 0.0) if getattr(pro_res, "rr", None) is not None else None,
                                "net_profit_yen": float(getattr(pro_res, "net_profit_yen", 0.0) or 0.0) if getattr(pro_res, "net_profit_yen", None) is not None else None,
                            },
                        },
                        **payload_extra,
                    }

                    # ★NEW: behavior候補（acceptedが無いrunの保険）
                    last_behavior_any = _make_behavior_row_for_ui(
                        code=code,
                        ts_iso=ts_iso,
                        sim_order=sim_order_base,
                        replay=defaults.get("replay"),
                        ml_ok=ml_ok,
                        p_win=p_win,
                        ev_pred=ev_pred,
                        p_tp_first=p_tp_first,
                        p_sl_first=p_sl_first,
                        ev_true=float(_ev_true_from_behavior(code)),
                        shape_entry_k=shape_entry_k,
                        shape_rr_target=shape_rr_target,
                        shape_tp_k=shape_tp_k,
                        shape_sl_k=shape_sl_k,
                    )

                    if dry_run:
                        self.stdout.write(
                            f"  reject_cash code={code} req={req_cash:.0f} cash_left={cash_before:.0f} entry_reason={entry_reason}"
                        )
                        continue

                    VirtualTrade.objects.update_or_create(
                        user=user,
                        run_id=run_id,
                        code=code,
                        defaults=defaults,
                    )
                    upserted += 1
                    continue

                # ---------- position limits ----------
                can, skip_info = mgr.can_open(code, risk_r=1.0)
                if not can:
                    skipped_limits += 1

                    defaults = _make_vtrade_defaults_base(
                        run_date=run_date,
                        trade_date=trade_date,
                        source="ai_simulate_auto",
                        mode=rec_mode,
                        code=code,
                        name=name or "",
                        sector=sector or "",
                        side=side,
                        universe=str(universe or ""),
                        style=str(style or ""),
                        horizon=str(horizon or ""),
                        topk=topk if isinstance(topk, int) else _safe_int(topk),
                        score=score if score is None else float(score),
                        score_100=score_100 if score_100 is None else int(score_100),
                        stars=stars if stars is None else int(stars),
                        mode_period=mode_period,
                        mode_aggr=mode_aggr,
                        entry=entry if entry is None else float(entry),
                        tp=tp if tp is None else float(tp),
                        sl=sl if sl is None else float(sl),
                        last_close=last_close if last_close is None else float(last_close),
                        opened_at_dt=opened_at_dt,
                        entry_reason=entry_reason,
                        ev_true_pro=_ev_true_from_behavior(code),
                    )
                    defaults.update(
                        qty_pro=int(getattr(pro_res, "qty_pro", 0) or 0),
                        required_cash_pro=float(req_cash),
                        est_pl_pro=float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                        est_loss_pro=float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                    )
                    defaults["replay"] = {
                        "sim_order": sim_order_base,
                        "opened_at_local": str(timezone.localtime(opened_at_dt)),
                        "pro": {
                            **run_common_pro_meta,
                            "status": "skipped_by_limits",

                            # ★② ML監査
                            "ml": {
                                "ok": bool(ml_ok),
                                "reason": str(ml_reason),
                                "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                                "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                                "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                                "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                                "ev_true": float(_ev_true_from_behavior(code)),
                            },

                            "skip": {
                                "reason_code": getattr(skip_info, "reason_code", "unknown") if skip_info else "unknown",
                                "reason_msg": getattr(skip_info, "reason_msg", "") if skip_info else "",
                                "open_count": getattr(skip_info, "open_count", None) if skip_info else None,
                                "total_risk_r": getattr(skip_info, "total_risk_r", None) if skip_info else None,
                            },
                            "cap": {
                                "remaining_slots": int(max(1, max_positions - mgr.count_open())),
                                "target_per_trade_yen": float(target_per_trade_yen),
                                "cap_yen": float(cap_yen),
                                "min_yen": float(min_yen),
                                "cap_reason": str(cap_reason or ""),
                            },
                            "cash": {
                                "cash_before": cash_before,
                                "required_cash_pro": req_cash,
                                "cash_after": cash_before,
                            },
                            "sizing": {
                                "qty_pro": int(getattr(pro_res, "qty_pro", 0) or 0),
                                "required_cash_pro": req_cash,
                                "est_pl_pro": float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                                "est_loss_pro": float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                                "rr": float(getattr(pro_res, "rr", 0.0) or 0.0) if getattr(pro_res, "rr", None) is not None else None,
                                "net_profit_yen": float(getattr(pro_res, "net_profit_yen", 0.0) or 0.0) if getattr(pro_res, "net_profit_yen", None) is not None else None,
                            },
                        },
                        **payload_extra,
                    }

                    # ★NEW: behavior候補（acceptedが無いrunの保険）
                    last_behavior_any = _make_behavior_row_for_ui(
                        code=code,
                        ts_iso=ts_iso,
                        sim_order=sim_order_base,
                        replay=defaults.get("replay"),
                        ml_ok=ml_ok,
                        p_win=p_win,
                        ev_pred=ev_pred,
                        p_tp_first=p_tp_first,
                        p_sl_first=p_sl_first,
                        ev_true=float(_ev_true_from_behavior(code)),
                        shape_entry_k=shape_entry_k,
                        shape_rr_target=shape_rr_target,
                        shape_tp_k=shape_tp_k,
                        shape_sl_k=shape_sl_k,
                    )

                    if dry_run:
                        self.stdout.write(
                            f"  skip_limits code={code} req={req_cash:.0f} cash_left={cash_before:.0f} "
                            f"open_now={mgr.count_open()} risk={mgr.total_risk_r:.2f} entry_reason={entry_reason}"
                        )
                        continue

                    VirtualTrade.objects.update_or_create(
                        user=user,
                        run_id=run_id,
                        code=code,
                        defaults=defaults,
                    )
                    upserted += 1
                    continue

                # ---------- accepted -> occupy slot + cash consume ----------
                cash_after = cash_left - req_cash
                if cash_after < 0:
                    cash_after = 0.0

                mgr.open(code, risk_r=1.0, trade_date=str(trade_date_str), opened_at=str(opened_at_dt))
                accepted += 1
                cash_left = cash_after

                # ---------- JSONL record (PRO主役) ----------
                rec: Dict[str, Any] = {
                    **sim_order_base,

                    # ★PRO（統一口座）
                    "qty_pro": int(getattr(pro_res, "qty_pro", 0) or 0),
                    "required_cash_pro": float(req_cash),
                    "est_pl_pro": float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                    "est_loss_pro": float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                    "pro_equity_yen": float(total_equity_yen),
                    "pro_notional_cap_yen": float(cash_start),
                    "pro_mode": str(learn_mode),
                    "policy_mode": str(policy.get("mode") or "short_aggressive"),

                    # ★資金監査（このrunの資金状態）
                    "pro_cash_before": cash_before,
                    "pro_cash_after": cash_after,

                    # ★C: cap監査
                    "pro_target_per_trade_yen": float(target_per_trade_yen),
                    "pro_cap_yen": float(cap_yen),
                    "pro_cap_reason": str(cap_reason or ""),

                    # ★互換（参考）
                    "qty_rakuten": it.get("qty_rakuten"),
                    "qty_sbi": it.get("qty_sbi"),
                    "qty_matsui": it.get("qty_matsui"),
                    "required_cash_rakuten": it.get("required_cash_rakuten"),
                    "required_cash_sbi": it.get("required_cash_sbi"),
                    "required_cash_matsui": it.get("required_cash_matsui"),
                    "est_pl_rakuten": it.get("est_pl_rakuten"),
                    "est_pl_sbi": it.get("est_pl_sbi"),
                    "est_pl_matsui": it.get("est_pl_matsui"),
                    "est_loss_rakuten": it.get("est_loss_rakuten"),
                    "est_loss_sbi": it.get("est_loss_sbi"),
                    "est_loss_matsui": it.get("est_loss_matsui"),
                }

                defaults = _make_vtrade_defaults_base(
                    run_date=run_date,
                    trade_date=trade_date,
                    source="ai_simulate_auto",
                    mode=rec_mode,
                    code=code,
                    name=name or "",
                    sector=sector or "",
                    side=side,
                    universe=str(universe or ""),
                    style=str(style or ""),
                    horizon=str(horizon or ""),
                    topk=topk if isinstance(topk, int) else _safe_int(topk),
                    score=score if score is None else float(score),
                    score_100=score_100 if score_100 is None else int(score_100),
                    stars=stars if stars is None else int(stars),
                    mode_period=mode_period,
                    mode_aggr=mode_aggr,
                    entry=entry if entry is None else float(entry),
                    tp=tp if tp is None else float(tp),
                    sl=sl if sl is None else float(sl),
                    last_close=last_close if last_close is None else float(last_close),
                    opened_at_dt=opened_at_dt,
                    entry_reason=entry_reason,
                    ev_true_pro=_ev_true_from_behavior(code),
                )
                defaults.update(
                    qty_pro=int(getattr(pro_res, "qty_pro", 0) or 0),
                    required_cash_pro=float(req_cash),
                    est_pl_pro=float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                    est_loss_pro=float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                    qty_rakuten=it.get("qty_rakuten"),
                    qty_sbi=it.get("qty_sbi"),
                    qty_matsui=it.get("qty_matsui"),
                    required_cash_rakuten=it.get("required_cash_rakuten"),
                    required_cash_sbi=it.get("required_cash_sbi"),
                    required_cash_matsui=it.get("required_cash_matsui"),
                    est_pl_rakuten=it.get("est_pl_rakuten"),
                    est_pl_sbi=it.get("est_pl_sbi"),
                    est_pl_matsui=it.get("est_pl_matsui"),
                    est_loss_rakuten=it.get("est_loss_rakuten"),
                    est_loss_sbi=it.get("est_loss_sbi"),
                    est_loss_matsui=it.get("est_loss_matsui"),
                )
                defaults["replay"] = {
                    "sim_order": rec,
                    "trade_date_auto_reason": trade_date_reason,
                    "opened_at_local": str(timezone.localtime(opened_at_dt)),
                    "pro": {
                        **run_common_pro_meta,
                        "status": "accepted",

                        # ★② ML監査（acceptedでも残す）
                        "ml": {
                            "ok": bool(ml_ok),
                            "reason": str(ml_reason),
                            "p_win": (float(p_win) if (p_win is not None and ml_ok) else None),
                            "ev_pred": (float(ev_pred) if (ev_pred is not None and ml_ok) else None),
                            "p_tp_first": (float(p_tp_first) if (p_tp_first is not None and ml_ok) else None),
                            "p_sl_first": (float(p_sl_first) if (p_sl_first is not None and ml_ok) else None),
                            "ev_true": float(_ev_true_from_behavior(code)),
                        },

                        "cap": {
                            "remaining_slots": int(max(1, max_positions - (mgr.count_open() - 1))),
                            "target_per_trade_yen": float(target_per_trade_yen),
                            "cap_yen": float(cap_yen),
                            "min_yen": float(min_yen),
                            "cap_reason": str(cap_reason or ""),
                        },
                        "cash": {
                            "cash_before": cash_before,
                            "required_cash_pro": req_cash,
                            "cash_after": cash_after,
                        },
                        "limits": {
                            "max_positions": max_positions,
                            "max_total_risk_r": max_total_risk_r,
                            "open_count_after": mgr.count_open(),
                            "total_risk_after": float(mgr.total_risk_r),
                        },
                        "sizing": {
                            "qty_pro": int(getattr(pro_res, "qty_pro", 0) or 0),
                            "required_cash_pro": float(req_cash),
                            "est_pl_pro": float(getattr(pro_res, "est_pl_pro", 0.0) or 0.0),
                            "est_loss_pro": float(getattr(pro_res, "est_loss_pro", 0.0) or 0.0),
                            "rr": float(getattr(pro_res, "rr", 0.0) or 0.0) if getattr(pro_res, "rr", None) is not None else None,
                            "net_profit_yen": float(getattr(pro_res, "net_profit_yen", 0.0) or 0.0) if getattr(pro_res, "net_profit_yen", None) is not None else None,
                        },
                    },
                    **payload_extra,
                }

                # ★NEW: behavior候補（accepted優先で末尾1行を確実に更新）
                last_behavior_any = _make_behavior_row_for_ui(
                    code=code,
                    ts_iso=ts_iso,
                    sim_order=rec,
                    replay=defaults.get("replay"),
                    ml_ok=ml_ok,
                    p_win=p_win,
                    ev_pred=ev_pred,
                    p_tp_first=p_tp_first,
                    p_sl_first=p_sl_first,
                    ev_true=float(_ev_true_from_behavior(code)),
                    shape_entry_k=shape_entry_k,
                    shape_rr_target=shape_rr_target,
                    shape_tp_k=shape_tp_k,
                    shape_sl_k=shape_sl_k,
                )
                last_behavior_accepted = last_behavior_any

                if dry_run:
                    self.stdout.write(
                        f"  accept code={code} ev={_ev_true_from_behavior(code):.3f} "
                        f"qty_pro={int(getattr(pro_res,'qty_pro',0) or 0)} req={req_cash:.0f} "
                        f"cap={cap_yen:.0f} cash_before={cash_before:.0f} cash_after={cash_after:.0f} "
                        f"open_now={mgr.count_open()} risk={mgr.total_risk_r:.2f} mode={learn_mode} cap_reason={cap_reason or '-'} "
                        f"entry_reason={entry_reason} ml_ok={ml_ok} ml_reason={ml_reason} "
                        f"shape(entry_k={shape_entry_k}, rr={shape_rr_target}, tp_k={shape_tp_k}, sl_k={shape_sl_k})"
                    )
                    continue

                # JSONL（acceptedのみ）
                if fw is not None:
                    fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1

                # DB upsert
                VirtualTrade.objects.update_or_create(
                    user=user,
                    run_id=run_id,
                    code=code,
                    defaults=defaults,
                )
                upserted += 1

        finally:
            if fw is not None:
                fw.close()

        # ★NEW: runの最後に behavior/latest_behavior.jsonl を必ず更新（accepted優先）
        if not dry_run:
            row_to_write = last_behavior_accepted or last_behavior_any
            if isinstance(row_to_write, dict) and row_to_write:
                _append_latest_behavior_jsonl(row_to_write)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[ai_simulate_auto] DRY-RUN done run_id={run_id} run_date={run_date_str} trade_date={trade_date_str}({trade_date_reason}) "
                    f"pro_mode={learn_mode} accepted={accepted} rejected_by_cash={rejected_by_cash} skipped_filter={skipped_pro_filter} skipped_limits={skipped_limits} "
                    f"cash_left_end={cash_left:.0f} open_after={mgr.count_open()} total_risk_after={mgr.total_risk_r:.2f}"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] run_id={run_id} run_date={run_date_str} trade_date={trade_date_str}({trade_date_reason}) user_id={user_id} "
                f"policy={policy_path} pro_mode={learn_mode} cap_total={cash_start:.0f} reserve={float(reserve_cash_yen):.0f} used_before={cash_used_before:.0f} cash_left_end={cash_left:.0f} "
                f"limits=(pos={max_positions}, risk={max_total_risk_r:.2f}R) "
                f"accepted={accepted} rejected_by_cash={rejected_by_cash} skipped_filter={skipped_pro_filter} skipped_limits={skipped_limits} "
                f"jsonl_written={written} db_upserted={upserted} -> {out_path}"
            )
        )