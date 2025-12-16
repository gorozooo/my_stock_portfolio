# -*- coding: utf-8 -*-
"""
ai_simulate_auto

紙トレ自動エントリー（DEMO）コマンド。

役割:
- media/aiapp/picks/latest_full.json を読み込む
- TopK の注文を JSONL に起票する（既存パイプライン互換）
- 同時に VirtualTrade(DB) に "OPEN" として同期する（UI/⭐️集計用）

★ 本体化（Step1）対応：
- VirtualTrade.replay に「特徴量スナップショット（安定性用）」を保存
- VirtualTrade.replay に「Entry→TP/SL 距離指標（距離妥当性用）」を保存
  ※ yfinance等の取得失敗でも落とさず、保存できる範囲だけ保存する

★ 追加（プロ仕様）：
- EV_true の高い順に候補を処理
- 同時ポジション制限（max_positions / max_total_risk_r）を適用
- 既にOPEN扱いの銘柄は重複禁止
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.position_limits import LimitConfig, PositionLimitManager

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


# ========= パス定義（MEDIA_ROOT ベース） =========
PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"
SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

# ========= policy =========
POLICY_DIR = Path(getattr(settings, "BASE_DIR", Path("."))) / "aiapp" / "policies"
DEFAULT_POLICY_FILE = "short_aggressive.yml"


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


# ========= policy読み込み（limits） =========
def _load_limits_from_policy() -> LimitConfig:
    """
    aiapp/policies/short_aggressive.yml から limits を読む。
    PyYAML が無い環境でも落とさず、デフォルトで動く。
    """
    path = POLICY_DIR / DEFAULT_POLICY_FILE
    cfg = LimitConfig()

    if not path.exists():
        return cfg

    try:
        import yaml  # type: ignore
    except Exception:
        return cfg

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return cfg

    try:
        limits = data.get("limits") or {}
        if isinstance(limits, dict):
            mp = limits.get("max_positions")
            mr = limits.get("max_total_risk_r")
            if mp is not None:
                cfg.max_positions = int(mp)
            if mr is not None:
                cfg.max_total_risk_r = float(mr)
    except Exception:
        pass

    return cfg


# ========= “既にOPEN”の銘柄コード一覧を可能な範囲で取る =========
def _get_open_codes_from_db(user) -> List[str]:
    """
    VirtualTrade のスキーマ差を吸収して「OPEN扱い」を推測する。
    1) closed_at があれば closed_at__isnull=True
    2) status/state があれば OPEN 系
    3) それも無ければ 直近 run_date の重複防止に留める（=空で返す）
    """
    try:
        field_names = {f.name for f in VirtualTrade._meta.get_fields()}  # type: ignore
    except Exception:
        field_names = set()

    qs = VirtualTrade.objects.filter(user=user)

    try:
        if "closed_at" in field_names:
            qs = qs.filter(closed_at__isnull=True)
        elif "closed_dt" in field_names:
            qs = qs.filter(closed_dt__isnull=True)
        elif "status" in field_names:
            qs = qs.filter(status__in=["OPEN", "open", "Open"])
        elif "state" in field_names:
            qs = qs.filter(state__in=["OPEN", "open", "Open"])
        else:
            return []
    except Exception:
        return []

    try:
        return [str(x) for x in qs.values_list("code", flat=True)]
    except Exception:
        return []


# ========= EV_true をソート用のスカラーにする =========
def _ev_scalar(it: Dict[str, Any]) -> float:
    """
    it["ev_true"] が
    - 数値 → そのまま
    - dict (r/m/s) → 平均（存在するものだけ）
    - 無い → -999
    """
    v = it.get("ev_true")
    if isinstance(v, (int, float)):
        return float(v)

    if isinstance(v, dict):
        vals: List[float] = []
        for k in ("r", "m", "s", "R", "M", "S"):
            x = v.get(k)
            fx = _safe_float(x)
            if fx is not None:
                vals.append(fx)
        if vals:
            return sum(vals) / float(len(vals))

    # 個別キーが来るパターンも拾う
    for key in ("ev_true_r", "ev_true_m", "ev_true_s"):
        fx = _safe_float(it.get(key))
        if fx is not None:
            return fx

    return -999.0


def _ev_parts(it: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    (ev_true_scalar, r, m, s)
    """
    scalar = _ev_scalar(it)
    v = it.get("ev_true")
    r = m = s = None
    if isinstance(v, dict):
        r = _safe_float(v.get("r") if "r" in v else v.get("R"))
        m = _safe_float(v.get("m") if "m" in v else v.get("M"))
        s = _safe_float(v.get("s") if "s" in v else v.get("S"))
    else:
        r = _safe_float(it.get("ev_true_r"))
        m = _safe_float(it.get("ev_true_m"))
        s = _safe_float(it.get("ev_true_s"))

    if scalar <= -998:
        scalar_out: Optional[float] = None
    else:
        scalar_out = scalar
    return scalar_out, r, m, s


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
            if x is None:
                return None
            if x != x:
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


class Command(BaseCommand):
    help = "AIフル自動シミュレ用：DEMO紙トレ注文を JSONL に起票 + VirtualTrade同期（制限付き）"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（指定がなければJSTの今日）")
        parser.add_argument("--overwrite", action="store_true", help="同じ日付の jsonl を上書き")
        parser.add_argument("--mode-period", type=str, default="short", help="short/mid/long（将来拡張）")
        parser.add_argument("--mode-aggr", type=str, default="aggr", help="aggr/norm/def（将来拡張）")

    def handle(self, *args, **options):
        run_date_str: str = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))

        mode_period: str = (options.get("mode_period") or "short").strip().lower()
        mode_aggr: str = (options.get("mode_aggr") or "aggr").strip().lower()

        trade_date_str = run_date_str

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

        # ★EV_true 高い順に並べる（無いものは後ろ）
        items.sort(key=_ev_scalar, reverse=True)

        User = get_user_model()
        user = User.objects.order_by("id").first()
        if not user:
            self.stdout.write(self.style.ERROR("[ai_simulate_auto] no user found"))
            return

        user_id = user.id

        style = (meta.get("style") or "aggressive")
        horizon = (meta.get("horizon") or "short")
        universe = (meta.get("universe") or "unknown")
        topk = meta.get("topk")

        run_id = dt_now_run_id(prefix="auto_demo")

        SIM_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SIM_DIR / f"sim_orders_{run_date_str}.jsonl"
        file_mode = "w" if overwrite else "a"

        ts_iso = dt_now_jst_iso()
        opened_at_dt = _parse_dt_iso(ts_iso) or timezone.now()

        run_date = _parse_date(run_date_str)
        trade_date = _parse_date(trade_date_str)

        # ===== 制限エンジン準備 =====
        limits_cfg = _load_limits_from_policy()
        pm = PositionLimitManager(limits_cfg)

        # 既存OPEN（可能なら）をロード
        open_codes = _get_open_codes_from_db(user)
        pm.load_open_positions({c: {"risk_r": 1.0} for c in open_codes}, total_risk_r=float(len(open_codes)))

        self.stdout.write(
            f"[ai_simulate_auto] limits: max_positions={limits_cfg.max_positions} "
            f"max_total_risk_r={limits_cfg.max_total_risk_r} "
            f"open_already={len(open_codes)}"
        )

        written = 0
        upserted = 0
        skipped_limit = 0

        with out_path.open(file_mode, encoding="utf-8") as fw:
            for it in items:
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

                qty_rakuten = it.get("qty_rakuten")
                qty_sbi = it.get("qty_sbi")
                qty_matsui = it.get("qty_matsui")

                est_pl_rakuten = it.get("est_pl_rakuten")
                est_pl_sbi = it.get("est_pl_sbi")
                est_pl_matsui = it.get("est_pl_matsui")

                est_loss_rakuten = it.get("est_loss_rakuten")
                est_loss_sbi = it.get("est_loss_sbi")
                est_loss_matsui = it.get("est_loss_matsui")

                required_cash_rakuten = it.get("required_cash_rakuten")
                required_cash_sbi = it.get("required_cash_sbi")
                required_cash_matsui = it.get("required_cash_matsui")

                score = it.get("score")
                score_100 = it.get("score_100")
                stars = it.get("stars")

                ev_scalar, ev_r, ev_m, ev_s = _ev_parts(it)

                # ===== 制限チェック（重複/最大数/合計R）=====
                ok, skip = pm.can_open(code, risk_r=1.0)
                if not ok:
                    skipped_limit += 1
                    # JSONLにも「スキップ理由」を残す（追跡用）
                    rec_skip: Dict[str, Any] = {
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
                        "ev_true": ev_scalar,
                        "ev_true_r": ev_r,
                        "ev_true_m": ev_m,
                        "ev_true_s": ev_s,
                        "skip_reason": skip.reason_code if skip else "limit",
                        "skip_msg": skip.reason_msg if skip else "limit",
                        "open_count": skip.open_count if skip else pm.count_open(),
                        "total_risk_r": skip.total_risk_r if skip else pm.total_risk_r,
                        "source": "ai_simulate_auto",
                    }
                    fw.write(json.dumps(rec_skip, ensure_ascii=False) + "\n")
                    written += 1
                    continue

                # ===== 通過したら “枠を確保” してから書く（順序バグ防止）=====
                pm.open(code, risk_r=1.0, ev_true=ev_scalar)

                rec: Dict[str, Any] = {
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
                    "qty_rakuten": qty_rakuten,
                    "qty_sbi": qty_sbi,
                    "qty_matsui": qty_matsui,
                    "est_pl_rakuten": est_pl_rakuten,
                    "est_pl_sbi": est_pl_sbi,
                    "est_pl_matsui": est_pl_matsui,
                    "est_loss_rakuten": est_loss_rakuten,
                    "est_loss_sbi": est_loss_sbi,
                    "est_loss_matsui": est_loss_matsui,
                    "required_cash_rakuten": required_cash_rakuten,
                    "required_cash_sbi": required_cash_sbi,
                    "required_cash_matsui": required_cash_matsui,
                    "score": score,
                    "score_100": score_100,
                    "stars": stars,
                    "style": style,
                    "horizon": horizon,
                    "universe": universe,
                    "topk": topk,
                    "ev_true": ev_scalar,
                    "ev_true_r": ev_r,
                    "ev_true_m": ev_m,
                    "ev_true_s": ev_s,
                    "skip_reason": None,
                    "source": "ai_simulate_auto",
                }

                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

                # ★ 本体化：安定性/距離妥当性の材料を作って replay に保存
                payload_extra = _build_feat_last_and_distance(
                    code=code,
                    entry=_safe_float(entry),
                    tp=_safe_float(tp),
                    sl=_safe_float(sl),
                    last_close=_safe_float(last_close),
                    atr_pick=_safe_float(atr_pick),
                )

                defaults = dict(
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
                    entry_px=entry if entry is None else float(entry),
                    tp_px=tp if tp is None else float(tp),
                    sl_px=sl if sl is None else float(sl),
                    last_close=last_close if last_close is None else float(last_close),
                    qty_rakuten=qty_rakuten if qty_rakuten is None else int(qty_rakuten),
                    qty_sbi=qty_sbi if qty_sbi is None else int(qty_sbi),
                    qty_matsui=qty_matsui if qty_matsui is None else int(qty_matsui),
                    required_cash_rakuten=required_cash_rakuten if required_cash_rakuten is None else float(required_cash_rakuten),
                    required_cash_sbi=required_cash_sbi if required_cash_sbi is None else float(required_cash_sbi),
                    required_cash_matsui=required_cash_matsui if required_cash_matsui is None else float(required_cash_matsui),
                    est_pl_rakuten=est_pl_rakuten if est_pl_rakuten is None else float(est_pl_rakuten),
                    est_pl_sbi=est_pl_sbi if est_pl_sbi is None else float(est_pl_sbi),
                    est_pl_matsui=est_pl_matsui if est_pl_matsui is None else float(est_pl_matsui),
                    est_loss_rakuten=est_loss_rakuten if est_loss_rakuten is None else float(est_loss_rakuten),
                    est_loss_sbi=est_loss_sbi if est_loss_sbi is None else float(est_loss_sbi),
                    est_loss_matsui=est_loss_matsui if est_loss_matsui is None else float(est_loss_matsui),
                    opened_at=opened_at_dt,
                    replay={
                        "sim_order": rec,
                        "ev_true": ev_scalar,
                        "ev_true_r": ev_r,
                        "ev_true_m": ev_m,
                        "ev_true_s": ev_s,
                        "limits": {
                            "max_positions": limits_cfg.max_positions,
                            "max_total_risk_r": limits_cfg.max_total_risk_r,
                        },
                        **payload_extra,
                    },
                )

                VirtualTrade.objects.update_or_create(
                    user=user,
                    run_id=run_id,
                    code=code,
                    defaults=defaults,
                )
                upserted += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] run_id={run_id} run_date={run_date_str} user_id={user_id} "
                f"jsonl_lines_written={written} db_upserted={upserted} skipped_by_limits={skipped_limit} -> {out_path}"
            )
        )