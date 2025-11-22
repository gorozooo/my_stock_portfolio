# aiapp/management/commands/ai_sim_eval.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

import yfinance as yf


def _parse_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


class Command(BaseCommand):
    """
    シミュレログ (/media/aiapp/simulate/*.jsonl) を読み取り、
    各レコードに「評価結果（終値ベースのPL / R / ラベル）」を付与するコマンド。

    使い方:
      python manage.py ai_sim_eval
      python manage.py ai_sim_eval --days 5
      python manage.py ai_sim_eval --force   （既に評価済みでも上書き）
    """

    help = "AIシミュレログに結果（PL / R / ラベル）を付与する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価に使う日数（何営業日後の終値を見るか）",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="既に eval_* フィールドがあるレコードも再評価する",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        force: bool = options["force"]

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(self.style.WARNING(f"[ai_sim_eval] シミュレディレクトリがありません: {sim_dir}"))
            return

        self.stdout.write(f"[ai_sim_eval] dir={sim_dir} days={horizon_days} force={force}")

        # code -> (yf_symbol, dataframe) キャッシュ
        price_cache: Dict[str, Tuple[str, Any]] = {}

        total = 0
        evaluated = 0

        for path in sorted(sim_dir.glob("*.jsonl")):
            self.stdout.write(f"  読み込み中: {path.name}")
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    読み込み失敗: {e}"))
                continue

            lines = text.splitlines()
            new_lines: List[str] = []

            for line in lines:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    # 壊れた行はそのまま残す
                    new_lines.append(raw)
                    continue

                total += 1

                # 既に評価済みならスキップ（force で強制上書き可能）
                if not force and (
                    "eval_label_rakuten" in rec
                    or "eval_label_matsui" in rec
                ):
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                    continue

                ok = self._evaluate_record(rec, horizon_days, price_cache)
                if ok:
                    evaluated += 1

                new_lines.append(json.dumps(rec, ensure_ascii=False))

            # バックアップを残しつつ上書き
            backup = path.with_suffix(path.suffix + ".bak")
            try:
                if backup.exists():
                    backup.unlink()
                path.replace(backup)
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                self.stdout.write(self.style.SUCCESS(f"  → 評価結果を書き込みました: {path.name} (backup: {backup.name})"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  書き込み失敗: {e}"))
                # 失敗した場合、バックアップを戻す
                if backup.exists() and not path.exists():
                    backup.replace(path)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[ai_sim_eval] 全レコード: {total}件 / 評価したレコード: {evaluated}件"))
        self.stdout.write(self.style.SUCCESS("[ai_sim_eval] 完了"))

    # ===========================================================
    # 1レコードの評価ロジック
    # ===========================================================

    def _evaluate_record(
        self,
        rec: Dict[str, Any],
        horizon_days: int,
        price_cache: Dict[str, Tuple[str, Any]],
    ) -> bool:
        """
        1つのシミュレレコードに対して、
        - horizon_days 営業日後の終値を取得
        - 楽天 / 松井 ごとに PL / R / ラベルを計算
        を行い、rec を破壊的に更新する。

        成功したら True を返す。
        """
        code = str(rec.get("code") or "").strip()
        entry = _parse_float(rec.get("entry"))
        if not code or entry is None:
            # コードまたはentryが無い場合は評価しない
            return False

        ts_str = rec.get("ts")
        if not isinstance(ts_str, str) or not ts_str:
            return False

        try:
            dt = timezone.datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            dt = timezone.localtime(dt)
        except Exception:
            return False

        # yfinance シンボル作成
        yf_symbol = f"{code}.T"

        # 価格データ取得（コード毎にキャッシュ）
        if yf_symbol not in price_cache:
            try:
                # 3ヶ月分の日足を取得してキャッシュ
                hist = yf.Ticker(yf_symbol).history(period="3mo", interval="1d")
                if hist is None or hist.empty:
                    price_cache[yf_symbol] = (yf_symbol, None)
                else:
                    price_cache[yf_symbol] = (yf_symbol, hist)
            except Exception:
                price_cache[yf_symbol] = (yf_symbol, None)

        _, df = price_cache.get(yf_symbol, (yf_symbol, None))
        if df is None or df.empty:
            return False

        # 「ts の翌営業日」をエントリ日とみなす
        trade_date = dt.date()

        # df.index は Timestamp。date()で比較して、trade_dateより後の行を抽出
        try:
            df_after = df[df.index.date > trade_date]
        except Exception:
            return False

        if df_after.empty:
            return False

        # horizon_days 番目の終値（0始まりなので -1）を取る。
        idx = min(max(horizon_days - 1, 0), len(df_after) - 1)
        row = df_after.iloc[idx]
        try:
            close_px = float(row["Close"])
        except Exception:
            return False

        close_dt = row.name  # Timestamp
        try:
            close_date_str = close_dt.date().isoformat()
        except Exception:
            close_date_str = str(close_dt)

        # 楽天 / 松井それぞれを評価
        for broker in ("rakuten", "matsui"):
            qty = _parse_float(rec.get(f"qty_{broker}"))
            est_loss = _parse_float(rec.get(f"est_loss_{broker}"))

            if qty is None or qty == 0:
                pl = 0.0
                r_val = None
                label = "no_position"
            else:
                pl = (close_px - entry) * qty
                if est_loss and est_loss != 0:
                    r_val = pl / est_loss
                else:
                    r_val = None

                if pl > 0:
                    label = "win"
                elif pl < 0:
                    label = "lose"
                else:
                    label = "flat"

            rec[f"eval_pl_{broker}"] = pl
            rec[f"eval_r_{broker}"] = r_val
            rec[f"eval_label_{broker}"] = label

        # 共通メタ
        rec["eval_horizon_days"] = horizon_days
        rec["eval_close_px"] = close_px
        rec["eval_close_date"] = close_date_str
        rec["eval_version"] = "v1"

        return True