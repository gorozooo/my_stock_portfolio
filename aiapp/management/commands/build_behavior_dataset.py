# aiapp/management/commands/build_behavior_dataset.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone


Number = Optional[float]


@dataclass
class SideRow:
    """
    学習用（1行 = 1トレード × 1口座）の行データ（PRO一択）
    """
    user_id: int
    ts: str
    mode: str           # "live" / "demo" / "other"
    broker: str         # "pro" 固定
    code: str
    name: str
    sector: Optional[str]
    price_date: Optional[str]

    entry: Optional[float]
    tp: Optional[float]
    sl: Optional[float]

    qty: float
    est_pl: Optional[float]
    est_loss: Optional[float]

    eval_pl: float
    eval_r: Optional[float]
    eval_label: str     # "win" / "lose" / "flat"

    eval_horizon_days: Optional[int]
    atr_14: Optional[float]
    slope_20: Optional[float]
    trend_daily: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "ts": self.ts,
            "mode": self.mode,
            "broker": self.broker,
            "code": self.code,
            "name": self.name,
            "sector": self.sector,
            "price_date": self.price_date,
            "entry": self.entry,
            "tp": self.tp,
            "sl": self.sl,
            "qty": self.qty,
            "est_pl": self.est_pl,
            "est_loss": self.est_loss,
            "eval_pl": self.eval_pl,
            "eval_r": self.eval_r,
            "eval_label": self.eval_label,
            "eval_horizon_days": self.eval_horizon_days,
            "atr_14": self.atr_14,
            "slope_20": self.slope_20,
            "trend_daily": self.trend_daily,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _get_nested(d: Any, *keys: str, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _is_pro_accepted(r: Dict[str, Any]) -> bool:
    """
    PRO一択のフィルタ条件：
    - replay.pro.status == "accepted" のみ採用
    """
    status = _get_nested(r, "replay", "pro", "status", default=None)
    if status is None:
        # 互換（万一上位に pro_status があるケース）
        status = r.get("pro_status")
    return str(status or "").lower() == "accepted"


def _price_date_of(r: Dict[str, Any]) -> Optional[str]:
    """
    ログの揺れ吸収：
    - price_date があればそれ
    - 無ければ trade_date
    """
    v = r.get("price_date")
    if v in (None, "", "null"):
        v = r.get("trade_date")
    if v in (None, "", "null"):
        return None
    return str(v)


def _dedup_records(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    PROシミュレレコードの重複除外。

    キー：
      (user_id, mode, code, price_date, entry[小数3桁丸め], qty_pro)

    → 同じキーのものは 1件にまとめる。
    """
    seen: set[Tuple[Any, ...]] = set()
    deduped: List[Dict[str, Any]] = []

    for r in raw_rows:
        entry = _safe_float(r.get("entry")) or 0.0
        key = (
            r.get("user_id"),
            (r.get("mode") or "").lower(),
            r.get("code"),
            _price_date_of(r),
            round(entry, 3),
            _safe_float(r.get("qty_pro")) or 0.0,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return deduped


class Command(BaseCommand):
    """
    /media/aiapp/simulate/*.jsonl を読み込み、
    PRO一択で：

    - 重複を除外した「行動データセット」
    - 学習用の「1トレード×PRO」データセット（SideRow）
    を /media/aiapp/behavior/ 配下に出力する。

    出力ファイル：
      - YYYYMMDD_behavior_dataset.jsonl
      - latest_behavior.jsonl
      - YYYYMMDD_behavior_side.jsonl
      - latest_behavior_side.jsonl

    注意：
    - replay.pro.status == "accepted" のみ採用（PROで採用されたものだけが学習/分析対象）
    - 楽天/SBI/松井のキーは一切参照しない
    """

    help = "AI シミュレログから（PRO一択の）行動データセット／学習用データセットを構築する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価対象の horizon_days（ラベル付け済み前提／ここでは主にログ用）",
        )
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（指定なしなら全ユーザー）",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        target_user: Optional[int] = options["user"]

        simulate_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
        behavior_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(
            f"[build_behavior_dataset] simulate_dir={simulate_dir} -> out_dir={behavior_dir} "
            f"(horizon_days={horizon_days}, user={target_user})"
        )

        if not simulate_dir.exists():
            self.stdout.write(self.style.WARNING("  シミュレディレクトリが存在しません。処理を終了します。"))
            return

        # ---------- シミュレ JSONL を読み込み ----------
        raw_rows: List[Dict[str, Any]] = []
        file_count = 0
        pro_skipped_not_accepted = 0
        pro_skipped_qty0 = 0

        for path in sorted(simulate_dir.glob("*.jsonl")):
            file_count += 1
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  読み込み失敗: {path.name} ({e})"))
                continue

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                if target_user is not None and rec.get("user_id") != target_user:
                    continue

                # PRO一択：accepted のみ
                if not _is_pro_accepted(rec):
                    pro_skipped_not_accepted += 1
                    continue

                qty_pro = _safe_float(rec.get("qty_pro")) or 0.0
                if qty_pro <= 0:
                    pro_skipped_qty0 += 1
                    continue

                raw_rows.append(rec)

        if not raw_rows:
            self.stdout.write(self.style.WARNING("  対象レコードがありませんでした。（PRO accepted / qty_pro>0 が0件）"))
            self.stdout.write(f"  読み込みファイル数: {file_count}")
            self.stdout.write(f"  除外: not_accepted={pro_skipped_not_accepted}, qty0={pro_skipped_qty0}")
            return

        self.stdout.write(f"  読み込みファイル数: {file_count} / PRO候補行数: {len(raw_rows)}")
        self.stdout.write(f"  除外: not_accepted={pro_skipped_not_accepted}, qty0={pro_skipped_qty0}")

        # ---------- 重複除外 ----------
        rows = _dedup_records(raw_rows)
        self.stdout.write(
            f"  重複除外後レコード数: {len(rows)} (差分: {len(raw_rows) - len(rows)})"
        )

        # ---------- 行動データセット（そのままの構造） ----------
        dataset_lines: List[str] = [json.dumps(r, ensure_ascii=False) for r in rows]

        today_str = timezone.localdate().strftime("%Y%m%d")
        dataset_path = behavior_dir / f"{today_str}_behavior_dataset.jsonl"
        latest_path = behavior_dir / "latest_behavior.jsonl"

        dataset_text = "\n".join(dataset_lines) + ("\n" if dataset_lines else "")

        dataset_path.write_text(dataset_text, encoding="utf-8")
        latest_path.write_text(dataset_text, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"  行動データセットを書き出しました: {dataset_path.name} (件数: {len(rows)})"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  latest_behavior.jsonl を更新しました（{len(rows)} 件）"
            )
        )

        # ---------- 学習用 Side データセット（PRO一択） ----------
        side_rows: List[SideRow] = []
        side_skipped_no_eval = 0

        for r in rows:
            user_id = int(r.get("user_id") or 0)
            ts = str(r.get("ts") or "")
            mode = (str(r.get("mode") or "") or "").lower() or "other"
            if mode not in ("live", "demo"):
                mode = "other"

            base_kwargs = dict(
                user_id=user_id,
                ts=ts,
                mode=mode,
                code=str(r.get("code") or ""),
                name=str(r.get("name") or ""),
                sector=(r.get("sector") or None),
                price_date=_price_date_of(r),
                entry=_safe_float(r.get("entry")),
                tp=_safe_float(r.get("tp")),
                sl=_safe_float(r.get("sl")),
                eval_horizon_days=r.get("eval_horizon_days"),
                atr_14=_safe_float(r.get("atr_14")),
                slope_20=_safe_float(r.get("slope_20")),
                trend_daily=(r.get("trend_daily") or None),
            )

            side = self._build_side_row_pro(r, base_kwargs=base_kwargs)
            if side is not None:
                side_rows.append(side)
            else:
                side_skipped_no_eval += 1

        side_lines = [json.dumps(sr.to_dict(), ensure_ascii=False) for sr in side_rows]

        side_path = behavior_dir / f"{today_str}_behavior_side.jsonl"
        latest_side_path = behavior_dir / "latest_behavior_side.jsonl"

        side_text = "\n".join(side_lines) + ("\n" if side_lines else "")

        side_path.write_text(side_text, encoding="utf-8")
        latest_side_path.write_text(side_text, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"  学習用データセットを書き出しました: {side_path.name} (件数: {len(side_rows)})"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  latest_behavior_side.jsonl を更新しました（{len(side_rows)} 件）"
            )
        )
        self.stdout.write(f"  学習対象外（評価なし/未確定）: {side_skipped_no_eval} 件")

        self.stdout.write(self.style.SUCCESS("[build_behavior_dataset] 完了"))

    # =========================================================
    # PROサイド行の構築
    # =========================================================

    def _build_side_row_pro(
        self,
        r: Dict[str, Any],
        base_kwargs: Dict[str, Any],
    ) -> Optional[SideRow]:
        """
        1つのシミュレレコードから、PRO分の SideRow を作る。

        ルール（PRO一択）:
        - qty_pro <= 0 → 学習に使わない
        - eval_label_pro が win/lose/flat 以外 → 学習に使わない
        - eval_pl_pro が無い → 学習に使わない

        ※ログの揺れ吸収:
        - eval_label_pro が無ければ eval_label を見る（最後の保険）
        - eval_pl_pro が無ければ eval_pl を見る
        - eval_r_pro  が無ければ eval_r を見る
        """
        qty = _safe_float(r.get("qty_pro")) or 0.0
        if qty <= 0:
            return None

        label = (r.get("eval_label_pro") or r.get("eval_label") or "").lower()
        if label not in ("win", "lose", "flat"):
            return None

        eval_pl = _safe_float(r.get("eval_pl_pro"))
        if eval_pl is None:
            eval_pl = _safe_float(r.get("eval_pl"))
        if eval_pl is None:
            return None

        eval_r = _safe_float(r.get("eval_r_pro"))
        if eval_r is None:
            eval_r = _safe_float(r.get("eval_r"))

        est_pl = _safe_float(r.get("est_pl_pro"))
        est_loss = _safe_float(r.get("est_loss_pro"))

        return SideRow(
            broker="pro",
            qty=float(qty),
            est_pl=est_pl,
            est_loss=est_loss,
            eval_pl=float(eval_pl),
            eval_r=eval_r,
            eval_label=label,
            **base_kwargs,
        )