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
    学習用（1行 = 1トレード × 1ブローカー）の行データ
    """
    user_id: int
    ts: str
    mode: str           # "live" / "demo" / "other"
    broker: str         # "rakuten" / "matsui"
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
    slope_25: Optional[float]
    ret_20: Optional[float]
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
            "slope_25": self.slope_25,
            "ret_20": self.ret_20,
            "trend_daily": self.trend_daily,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _dedup_records(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    シミュレレコードの重複除外。

    キー：
      (user_id, mode, code, price_date, entry[小数3桁丸め],
       qty_rakuten, qty_matsui)

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
            r.get("price_date"),
            round(entry, 3),
            _safe_float(r.get("qty_rakuten")) or 0.0,
            _safe_float(r.get("qty_matsui")) or 0.0,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return deduped


def _pick_any(r: Dict[str, Any], *keys: str) -> Any:
    """
    複数キー候補から、最初に見つかった値を返す。
    """
    for k in keys:
        if k in r:
            return r.get(k)
    return None


class Command(BaseCommand):
    """
    /media/aiapp/simulate/*.jsonl を読み込み、
    - 重複シミュレを除外した「行動データセット」
    - 学習用の「1トレード×1ブローカー」データセット
    を /media/aiapp/behavior/ 配下に出力する。

    出力ファイル：
      - YYYYMMDD_behavior_dataset.jsonl
      - latest_behavior.jsonl                （上記のコピー）
      - YYYYMMDD_behavior_side.jsonl        （学習用：SideRow）
      - latest_behavior_side.jsonl          （上記のコピー）
    """

    help = "AI シミュレログから行動データセット／学習用データセットを構築する"

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

                raw_rows.append(rec)

        if not raw_rows:
            self.stdout.write(self.style.WARNING("  対象レコードがありませんでした。"))
            return

        self.stdout.write(f"  読み込みファイル数: {file_count} / 行数: {len(raw_rows)}")

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
                f"  行動データセットを書き出しました: {dataset_path.name} "
                f"(件数: {len(rows)})"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  latest_behavior.jsonl を更新しました（{len(rows)} 件）"
            )
        )

        # ---------- 学習用 Side データセット ----------
        side_rows: List[SideRow] = []

        for r in rows:
            user_id = int(r.get("user_id") or 0)
            ts = str(r.get("ts") or "")
            mode = (str(r.get("mode") or "") or "").lower() or "other"
            if mode not in ("live", "demo"):
                mode = "other"

            # ★ 新キー優先（無ければ旧互換）
            atr_14 = _safe_float(_pick_any(r, "atr_14", "ATR14"))
            slope_25 = _safe_float(_pick_any(r, "slope_25", "SLOPE_25", "slope_20", "SLOPE_20"))
            ret_20 = _safe_float(_pick_any(r, "ret_20", "RET_20"))
            trend_daily = _pick_any(r, "trend_daily", "TREND_DAILY")

            base_kwargs = dict(
                user_id=user_id,
                ts=ts,
                mode=mode,
                code=str(r.get("code") or ""),
                name=str(r.get("name") or ""),
                sector=(r.get("sector") or None),
                price_date=str(r.get("price_date") or None),
                entry=_safe_float(r.get("entry")),
                tp=_safe_float(r.get("tp")),
                sl=_safe_float(r.get("sl")),
                eval_horizon_days=r.get("eval_horizon_days"),
                atr_14=atr_14,
                slope_25=slope_25,
                ret_20=ret_20,
                trend_daily=str(trend_daily) if trend_daily is not None else None,
            )

            # --- 楽天側 ---
            side = self._build_side_row(r, broker="rakuten", base_kwargs=base_kwargs)
            if side is not None:
                side_rows.append(side)

            # --- 松井側 ---
            side = self._build_side_row(r, broker="matsui", base_kwargs=base_kwargs)
            if side is not None:
                side_rows.append(side)

        side_lines = [json.dumps(sr.to_dict(), ensure_ascii=False) for sr in side_rows]

        side_path = behavior_dir / f"{today_str}_behavior_side.jsonl"
        latest_side_path = behavior_dir / "latest_behavior_side.jsonl"

        side_text = "\n".join(side_lines) + ("\n" if side_lines else "")

        side_path.write_text(side_text, encoding="utf-8")
        latest_side_path.write_text(side_text, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"  学習用データセットを書き出しました: {side_path.name} "
                f"(件数: {len(side_rows)})"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  latest_behavior_side.jsonl を更新しました（{len(side_rows)} 件）"
            )
        )

        self.stdout.write(self.style.SUCCESS("[build_behavior_dataset] 完了"))

    # =========================================================
    # サイド行の構築
    # =========================================================

    def _build_side_row(
        self,
        r: Dict[str, Any],
        broker: str,
        base_kwargs: Dict[str, Any],
    ) -> Optional[SideRow]:
        """
        1つのシミュレレコードから、指定ブローカー分の SideRow を作る。

        - qty == 0 → 学習に使わない（ポジションなし）
        - eval_label が win/lose/flat 以外 → 学習に使わない
        """
        if broker == "rakuten":
            qty_key = "qty_rakuten"
            est_pl_key = "est_pl_rakuten"
            est_loss_key = "est_loss_rakuten"
            eval_pl_key = "eval_pl_rakuten"
            eval_r_key = "eval_r_rakuten"
            eval_label_key = "eval_label_rakuten"
            broker_name = "rakuten"
        else:
            qty_key = "qty_matsui"
            est_pl_key = "est_pl_matsui"
            est_loss_key = "est_loss_matsui"
            eval_pl_key = "eval_pl_matsui"
            eval_r_key = "eval_r_matsui"
            eval_label_key = "eval_label_matsui"
            broker_name = "matsui"

        qty = _safe_float(r.get(qty_key)) or 0.0
        if qty == 0:
            return None

        eval_label = (r.get(eval_label_key) or "").lower()
        if eval_label not in ("win", "lose", "flat"):
            return None

        eval_pl = _safe_float(r.get(eval_pl_key))
        if eval_pl is None:
            return None

        eval_r = _safe_float(r.get(eval_r_key))

        est_pl = _safe_float(r.get(est_pl_key))
        est_loss = _safe_float(r.get(est_loss_key))

        return SideRow(
            broker=broker_name,
            qty=qty,
            est_pl=est_pl,
            est_loss=est_loss,
            eval_pl=float(eval_pl),
            eval_r=eval_r,
            eval_label=eval_label,
            **base_kwargs,
        )