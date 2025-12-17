# aiapp/management/commands/ai_simulate_auto.py
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

★ 追加（今回）：
- 引け後などで手動実行した場合、trade_date を “翌営業日” に自動補正
  （--date を明示した場合は補正しない＝指定日を尊重）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade

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


# ========= 営業日（簡易：土日除外） =========
def _next_business_day(d):
    """
    JPX の厳密な休場日は見ない（まずは土日だけ除外）。
    """
    from datetime import timedelta

    x = d
    while True:
        x = x + timedelta(days=1)
        if x.weekday() < 5:
            return x


def _auto_trade_date_str_if_needed(run_date_str: str, *, date_given: bool) -> str:
    """
    --date を指定していない場合のみ、
    実行時刻が引け後なら trade_date を翌営業日に寄せる。
    """
    if date_given:
        return run_date_str

    now = _now_jst()
    run_d = _parse_date(run_date_str)

    # 15:00以降（引け後）なら翌営業日へ
    # ※ ざっくり運用に寄せる（朝cron運用なら常に当日）
    if (now.hour, now.minute) >= (15, 0):
        td = _next_business_day(run_d)
        return td.isoformat()

    # 寄り前/場中は当日
    return run_date_str


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
                                    if fv != fv:  # NaN
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
    help = "AIフル自動シミュレ用：DEMO紙トレ注文を JSONL に起票 + VirtualTrade同期"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（指定がなければJSTの今日）")
        parser.add_argument("--overwrite", action="store_true", help="同じ日付の jsonl を上書き")
        parser.add_argument("--mode-period", type=str, default="short", help="short/mid/long（将来拡張）")
        parser.add_argument("--mode-aggr", type=str, default="aggr", help="aggr/norm/def（将来拡張）")

    def handle(self, *args, **options):
        date_given = bool(options.get("date"))

        run_date_str: str = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))

        # ★追加：trade_date 自動補正（引け後なら翌営業日）
        trade_date_str: str = _auto_trade_date_str_if_needed(run_date_str, date_given=date_given)

        mode_period: str = (options.get("mode_period") or "short").strip().lower()
        mode_aggr: str = (options.get("mode_aggr") or "aggr").strip().lower()

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

        written = 0
        upserted = 0

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
                atr_pick = it.get("atr")  # picks_build が入れている想定（無ければ None）

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

                rec: Dict[str, Any] = {
                    "user_id": user_id,
                    "mode": rec_mode,
                    "ts": ts_iso,
                    "run_date": run_date_str,
                    "trade_date": trade_date_str,  # ★補正後
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
                    trade_date=trade_date,  # ★補正後
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
                        **payload_extra,  # feat_last / distance
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
                f"[ai_simulate_auto] run_id={run_id} run_date={run_date_str} trade_date={trade_date_str} user_id={user_id} "
                f"jsonl_written={written} db_upserted={upserted} -> {out_path}"
            )
        )