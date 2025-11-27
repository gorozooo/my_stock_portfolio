# aiapp/management/commands/ai_sim_eval.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

# ---- 新しく作ったサービスを使用（中で Level3 ロジックを呼ぶ想定）----
from aiapp.services.sim_eval_service import eval_sim_record


def _ensure_trade_date(rec: Dict[str, Any]) -> None:
    """
    Level3 評価で必要になる trade_date を、
    古いログでもできるだけ補完する。

    優先順位:
      1) 既に trade_date があればそのまま
      2) run_date
      3) price_date
      4) ts(ISO文字列) の日付部分
    """
    # 1) すでに trade_date があるなら何もしない
    td = rec.get("trade_date")
    if isinstance(td, str) and td:
        return

    # 2) run_date を優先（ai_simulate_auto が入れてくる想定）
    run_date = rec.get("run_date")
    if isinstance(run_date, str) and run_date:
        rec["trade_date"] = run_date
        return

    # 3) price_date （AI Picks 由来の価格日）
    price_date = rec.get("price_date")
    if isinstance(price_date, str) and price_date:
        rec["trade_date"] = price_date
        return

    # 4) ts から日付を引っこ抜く
    ts_str = rec.get("ts")
    if isinstance(ts_str, str) and ts_str:
        try:
            dt = timezone.datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            rec["trade_date"] = timezone.localtime(dt).date().isoformat()
            return
        except Exception:
            # ts が壊れていた場合はどうしようもないので諦める
            pass

    # ここまで来たら trade_date は設定できなかったが、
    # eval_sim_record 側で最後のフォールバックをしてもらう前提。
    return


class Command(BaseCommand):
    """
    シミュレログ (/media/aiapp/simulate/*.jsonl) を読み取り、
    sim_eval_service（Level3: 5分足ベース）で計算した結果
    （win/lose/flat・PL・TP/SL 到達日・exit_reason 等）を rec に付与して書き戻す。

    使い方:
      python manage.py ai_sim_eval
      python manage.py ai_sim_eval --days 5
      python manage.py ai_sim_eval --force
    """

    help = "AIシミュレログに結果（PL / ラベル / exit情報）を付与する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価に使う営業日数（何営業日後の値動きまでチェックするか）",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="既に eval_* が付与されていても再評価する",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        force: bool = options["force"]

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"[ai_sim_eval] シミュレディレクトリが存在しません: {sim_dir}"
                )
            )
            return

        self.stdout.write(
            f"[ai_sim_eval] dir={sim_dir} horizon_days={horizon_days} force={force}"
        )

        total = 0
        evaluated = 0

        # ===== 全 JSONL ファイルを処理 =====
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
                    rec: Dict[str, Any] = json.loads(raw)
                except Exception:
                    # 壊れた行はそのまま残す
                    new_lines.append(raw)
                    continue

                total += 1

                # ===== 既存評価がある場合のスキップ判定 =====
                already_has_eval = (
                    "eval_label_rakuten" in rec
                    or "eval_label_matsui" in rec
                    or "eval_close_px" in rec
                )

                if already_has_eval and not force:
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                    continue

                # ===== Level3 用に trade_date を補完 =====
                _ensure_trade_date(rec)

                # ===== sim_eval_service に評価させる =====
                try:
                    updated = eval_sim_record(rec, horizon_days=horizon_days)
                except Exception as e:
                    code = rec.get("code")
                    ts = rec.get("ts")
                    self.stdout.write(
                        self.style.ERROR(
                            f"    評価中にエラー: {e} "
                            f"(file={path.name}, code={code}, ts={ts})"
                        )
                    )
                    # 壊さないために rec をそのまま使用
                    updated = rec

                # updated は rec に eval_ 系が付いた dict
                new_lines.append(json.dumps(updated, ensure_ascii=False))
                evaluated += 1

            # ===== バックアップ作成 & 上書き =====
            backup = path.with_suffix(path.suffix + ".bak")

            try:
                if backup.exists():
                    backup.unlink()

                path.replace(backup)  # 現ファイル → backup
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  → 書き込み完了: {path.name} (backup: {backup.name})"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  書き込み失敗: {e}"))
                # 書き込み失敗時はバックアップを復旧
                if backup.exists() and not path.exists():
                    backup.replace(path)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_sim_eval] 全レコード: {total}件 / 評価したレコード: {evaluated}件"
            )
        )
        self.stdout.write(self.style.SUCCESS("[ai_sim_eval] 完了"))