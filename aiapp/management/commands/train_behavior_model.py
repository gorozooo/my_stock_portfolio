from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone


Number = Optional[float]


@dataclass
class AggStat:
    trials: int = 0
    wins: int = 0
    sum_pl: float = 0.0
    sum_r: float = 0.0
    cnt_r: int = 0

    def to_dict(self) -> Dict[str, Any]:
        win_rate = (self.wins / self.trials * 100.0) if self.trials > 0 else 0.0
        avg_pl = (self.sum_pl / self.trials) if self.trials > 0 else 0.0
        avg_r = (self.sum_r / self.cnt_r) if self.cnt_r > 0 else 0.0
        return {
            "trials": self.trials,
            "wins": self.wins,
            "win_rate": win_rate,
            "avg_pl": avg_pl,
            "avg_r": avg_r,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_dt(ts_str: str) -> Optional[timezone.datetime]:
    if not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _bucket_time_of_day(ts_str: str) -> str:
    """
    時間帯をざっくり3区分＋その他に分ける。
    """
    dt = _parse_dt(ts_str)
    if dt is None:
        return "時間外/その他"
    h = dt.hour * 60 + dt.minute
    if 9 * 60 <= h < 11 * 60 + 30:
        return "前場寄り〜11:30"
    if 11 * 60 + 30 <= h < 13 * 60:
        return "お昼〜後場寄り"
    if 13 * 60 <= h <= 15 * 60:
        return "後場〜大引け"
    return "時間外/その他"


def _bucket_atr(atr: Optional[float]) -> str:
    if atr is None:
        return "不明"
    if atr < 1.0:
        return "ATR 〜1%"
    if atr < 2.0:
        return "ATR 1〜2%"
    if atr < 3.0:
        return "ATR 2〜3%"
    return "ATR 3%以上"


def _bucket_slope(slope: Optional[float]) -> str:
    if slope is None:
        return "不明"
    if slope < 0:
        return "下向き"
    if slope < 5:
        return "緩やかな上向き"
    if slope < 10:
        return "強めの上向き"
    return "急騰寄り"


def _get_qty_pro(rec: Dict[str, Any]) -> float:
    """
    PRO一択の数量を取得。
    - 新: qty
    - 旧: qty_pro
    - 最終フォールバック: qty_rakuten + qty_sbi + qty_matsui
    """
    v = _safe_float(rec.get("qty"))
    if v is not None:
        return float(v)

    v = _safe_float(rec.get("qty_pro"))
    if v is not None:
        return float(v)

    # 旧データ救済（画面はPRO扱いに統合する）
    q = 0.0
    for k in ("qty_rakuten", "qty_sbi", "qty_matsui"):
        q += float(_safe_float(rec.get(k)) or 0.0)
    return float(q)


def _get_eval_label_pro(rec: Dict[str, Any]) -> str:
    """
    PRO一択の勝敗ラベルを取得。
    - 新: eval_label
    - 旧: eval_label_pro
    - 最終フォールバック: eval_label_rakuten / sbi / matsui から合成
    """
    v = rec.get("eval_label")
    if v is None:
        v = rec.get("eval_label_pro")
    if v is not None:
        s = str(v).strip().lower()
        if s:
            return s

    # 旧データ救済
    labels: List[str] = []
    for k in ("eval_label_rakuten", "eval_label_sbi", "eval_label_matsui"):
        x = rec.get(k)
        if x is None:
            continue
        s = str(x).strip().lower()
        if s:
            labels.append(s)

    if not labels:
        return "none"

    sset = set(labels)
    if sset <= {"no_position"}:
        return "no_position"
    if "win" in sset and "lose" in sset:
        # 混在は学習から外す（ノイズ）
        return "mixed"
    if "win" in sset:
        return "win"
    if "lose" in sset:
        return "lose"
    if "flat" in sset and (sset <= {"flat"}):
        return "flat"
    if "flat" in sset:
        return "flat"
    return "none"


def _get_eval_pl_pro(rec: Dict[str, Any]) -> Optional[float]:
    """
    PRO一択のPLを取得。
    - 新: eval_pl
    - 旧: eval_pl_pro
    - 最終フォールバック: eval_pl_rakuten + eval_pl_sbi + eval_pl_matsui
    """
    v = _safe_float(rec.get("eval_pl"))
    if v is not None:
        return float(v)

    v = _safe_float(rec.get("eval_pl_pro"))
    if v is not None:
        return float(v)

    # 旧データ救済
    total = 0.0
    found = False
    for k in ("eval_pl_rakuten", "eval_pl_sbi", "eval_pl_matsui"):
        x = _safe_float(rec.get(k))
        if x is None:
            continue
        found = True
        total += float(x)
    return float(total) if found else None


def _get_eval_r_pro(rec: Dict[str, Any]) -> Optional[float]:
    """
    PRO一択のRを取得。
    - 新: eval_r
    - 旧: eval_r_pro
    - 最終フォールバック: eval_r_rakuten / sbi / matsui の平均（非null）
    """
    v = _safe_float(rec.get("eval_r"))
    if v is not None:
        return float(v)

    v = _safe_float(rec.get("eval_r_pro"))
    if v is not None:
        return float(v)

    vals: List[float] = []
    for k in ("eval_r_rakuten", "eval_r_sbi", "eval_r_matsui"):
        x = _safe_float(rec.get(k))
        if x is not None:
            vals.append(float(x))
    if not vals:
        return None
    return float(sum(vals) / len(vals))


class Command(BaseCommand):
    """
    latest_behavior_side.jsonl を読み込み、
    win / lose のトレードを使って
    ・PRO一択（broker固定="pro"）
    ・セクター別
    ・トレンド別
    ・時間帯別
    ・ATR帯別
    ・傾き帯別
    の統計モデルを JSON で保存する簡易学習コマンド。

    使い方:
      python manage.py train_behavior_model
      python manage.py train_behavior_model --user 1
    """

    help = "AI 行動データ（side形式）から簡易な統計モデルを学習して JSON に保存する（PRO一択）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（省略時は全ユーザー）",
        )

    def handle(self, *args, **options) -> None:
        user_id: Optional[int] = options.get("user")

        media_root = Path(settings.MEDIA_ROOT)
        behavior_dir = media_root / "aiapp" / "behavior"
        side_path = behavior_dir / "latest_behavior_side.jsonl"

        self.stdout.write(
            f"[train_behavior_model] MEDIA_ROOT={media_root} user={user_id}"
        )
        self.stdout.write(
            f"[train_behavior_model] 読み込み元: {side_path}"
        )

        if not side_path.exists():
            self.stdout.write(
                self.style.WARNING(
                    "[train_behavior_model] latest_behavior_side.jsonl が見つかりません。先に build_behavior_dataset を実行してください。"
                )
            )
            return

        # --------------------------------------------------
        # JSONL 読み込み & フィルタ（PRO一択）
        # --------------------------------------------------
        samples: List[Dict[str, Any]] = []
        try:
            text = side_path.read_text(encoding="utf-8")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[train_behavior_model] 読み込み失敗: {e}"))
            return

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            # user フィルタ
            if user_id is not None and rec.get("user_id") != user_id:
                continue

            qty = _get_qty_pro(rec)
            label = _get_eval_label_pro(rec)

            # 数量0 or 勝敗なしは学習対象外
            if qty <= 0:
                continue
            if label not in ("win", "lose"):
                continue

            # PRO一択に正規化（ここが重要）
            rec2 = dict(rec)
            rec2["broker"] = "pro"
            rec2["qty"] = qty
            rec2["eval_label"] = label
            rec2["eval_pl"] = _get_eval_pl_pro(rec)
            rec2["eval_r"] = _get_eval_r_pro(rec)

            samples.append(rec2)

        if not samples:
            self.stdout.write(
                self.style.WARNING("[train_behavior_model] 学習対象となる win/lose データがありません。")
            )
            return

        # --------------------------------------------------
        # 統計集計
        # --------------------------------------------------
        total_trades = 0
        total_wins = 0
        sum_pl_global = 0.0
        sum_r_global = 0.0
        cnt_r_global = 0

        broker_stats: Dict[str, AggStat] = defaultdict(AggStat)  # ← pro のみ
        sector_stats: Dict[str, AggStat] = defaultdict(AggStat)
        trend_stats: Dict[str, AggStat] = defaultdict(AggStat)
        time_stats: Dict[str, AggStat] = defaultdict(AggStat)
        atr_stats: Dict[str, AggStat] = defaultdict(AggStat)
        slope_stats: Dict[str, AggStat] = defaultdict(AggStat)

        for rec in samples:
            label = str(rec.get("eval_label") or "").lower()
            pl = _safe_float(rec.get("eval_pl")) or 0.0
            r_val = _safe_float(rec.get("eval_r"))

            # PRO固定
            broker = "pro"

            sector = str(rec.get("sector") or "(未分類)")
            trend = str(rec.get("trend_daily") or "不明")
            ts_str = str(rec.get("ts") or "")

            time_bucket = _bucket_time_of_day(ts_str)
            atr = _safe_float(rec.get("atr_14"))
            atr_bucket = _bucket_atr(atr)
            slope = _safe_float(rec.get("slope_20"))
            slope_bucket = _bucket_slope(slope)

            is_win = (label == "win")

            total_trades += 1
            if is_win:
                total_wins += 1

            sum_pl_global += pl
            if r_val is not None:
                sum_r_global += r_val
                cnt_r_global += 1

            # ブローカー別（PROのみ）
            bs = broker_stats[broker]
            bs.trials += 1
            if is_win:
                bs.wins += 1
            bs.sum_pl += pl
            if r_val is not None:
                bs.sum_r += r_val
                bs.cnt_r += 1

            # セクター別
            ss = sector_stats[sector]
            ss.trials += 1
            if is_win:
                ss.wins += 1
            ss.sum_pl += pl
            if r_val is not None:
                ss.sum_r += r_val
                ss.cnt_r += 1

            # トレンド別
            ts = trend_stats[trend]
            ts.trials += 1
            if is_win:
                ts.wins += 1
            ts.sum_pl += pl
            if r_val is not None:
                ts.sum_r += r_val
                ts.cnt_r += 1

            # 時間帯別
            tms = time_stats[time_bucket]
            tms.trials += 1
            if is_win:
                tms.wins += 1
            tms.sum_pl += pl
            if r_val is not None:
                tms.sum_r += r_val
                tms.cnt_r += 1

            # ATR帯別
            ats = atr_stats[atr_bucket]
            ats.trials += 1
            if is_win:
                ats.wins += 1
            ats.sum_pl += pl
            if r_val is not None:
                ats.sum_r += r_val
                ats.cnt_r += 1

            # 傾き帯別
            sls = slope_stats[slope_bucket]
            sls.trials += 1
            if is_win:
                sls.wins += 1
            sls.sum_pl += pl
            if r_val is not None:
                sls.sum_r += r_val
                sls.cnt_r += 1

        # グローバルKPI
        win_rate_global = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
        avg_pl_global = (sum_pl_global / total_trades) if total_trades > 0 else 0.0
        avg_r_global = (sum_r_global / cnt_r_global) if cnt_r_global > 0 else 0.0

        # --------------------------------------------------
        # モデルJSON構築（保存）
        # --------------------------------------------------
        now = timezone.now()
        date_tag = now.strftime("%Y%m%d")
        user_tag = f"u{user_id}" if user_id is not None else "uall"

        model_dir = behavior_dir / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        model_body: Dict[str, Any] = {
            "user_id": user_id,
            "total_trades": total_trades,
            "wins": total_wins,
            "win_rate": win_rate_global,
            "avg_pl": avg_pl_global,
            "avg_r": avg_r_global,
            "updated_at": now.isoformat(),
            "by_feature": {
                "broker": {k: v.to_dict() for k, v in broker_stats.items()},  # ← proのみ
                "sector": {k: v.to_dict() for k, v in sector_stats.items()},
                "trend_daily": {k: v.to_dict() for k, v in trend_stats.items()},
                "time_bucket": {k: v.to_dict() for k, v in time_stats.items()},
                "atr_bucket": {k: v.to_dict() for k, v in atr_stats.items()},
                "slope_bucket": {k: v.to_dict() for k, v in slope_stats.items()},
            },
        }

        out_path = model_dir / f"{date_tag}_behavior_model_{user_tag}.json"
        latest_path = model_dir / f"latest_behavior_model_{user_tag}.json"

        try:
            payload = json.dumps(model_body, ensure_ascii=False, indent=2)
            out_path.write_text(payload, encoding="utf-8")
            latest_path.write_text(payload, encoding="utf-8")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[train_behavior_model] 書き込み失敗: {e}"))
            return

        # --------------------------------------------------
        # サマリ出力
        # --------------------------------------------------
        self.stdout.write("")
        self.stdout.write("===== 行動モデル 学習サマリ（PRO一択） =====")
        self.stdout.write(f"  user_id      : {user_id}")
        self.stdout.write(f"  total_trades : {total_trades}")
        self.stdout.write(f"  wins         : {total_wins}")
        self.stdout.write(f"  win_rate     : {win_rate_global:.1f}%")
        self.stdout.write(f"  avg_pl       : {avg_pl_global:,.0f}")
        self.stdout.write(f"  avg_r        : {avg_r_global:.3f}")
        self.stdout.write("")
        self.stdout.write("  broker:")
        for broker, s in broker_stats.items():
            d = s.to_dict()
            self.stdout.write(
                f"    - {broker}: trials={d['trials']} wins={d['wins']} win_rate={d['win_rate']:.1f}% avg_r={d['avg_r']:.3f}"
            )
        self.stdout.write("")
        self.stdout.write(f"  → 保存先: {out_path}")
        self.stdout.write(f"  → latest: {latest_path}")
        self.stdout.write(self.style.SUCCESS("[train_behavior_model] 完了"))