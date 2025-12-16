from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, DefaultDict
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.sim_eval_service import eval_sim_record


def _ensure_trade_date(rec: Dict[str, Any]) -> None:
    td = rec.get("trade_date")
    if isinstance(td, str) and td:
        return

    run_date = rec.get("run_date")
    if isinstance(run_date, str) and run_date:
        rec["trade_date"] = run_date
        return

    price_date = rec.get("price_date")
    if isinstance(price_date, str) and price_date:
        rec["trade_date"] = price_date
        return

    ts_str = rec.get("ts")
    if isinstance(ts_str, str) and ts_str:
        try:
            dt = timezone.datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            rec["trade_date"] = timezone.localtime(dt).date().isoformat()
            return
        except Exception:
            pass


def _parse_dt_iso(ts: Any):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _clip(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _calc_ev_true_from_eval(
    *,
    entry_px: float | None,
    exit_px: float | None,
    tp_px: float | None,
    sl_px: float | None,
    exit_reason: str | None,
) -> float | None:
    """
    EV_true を「評価結果」から確定する（-1..+1）

    ルール:
    - no_position（刺さらず等）: 0.0
    - hit_tp: +1.0
    - hit_sl: -1.0
    - horizon_close: TP/SL 方向の進捗を -1..+1 へ正規化
        pl>=0: pl / (tp-entry)
        pl<0 : pl / (entry-sl)   (負)
      → 最後に -1..+1 にクリップ
    """
    e = entry_px
    x = exit_px
    t = tp_px
    s = sl_px
    r = (exit_reason or "").strip()

    if e is None or x is None:
        return None

    # eval_service 側の表記ゆらぎも吸収
    r_low = r.lower()

    if r_low in ("no_position", "no_entry", "not_filled", "skip"):
        return 0.0
    if r_low == "hit_tp":
        return 1.0
    if r_low == "hit_sl":
        return -1.0

    # horizon_close / その他
    pl = x - e

    # TP/SL が無ければ正規化不能 → 0 扱い（学習は別で弾ける）
    if t is None or s is None:
        return 0.0

    tp_dist = t - e
    sl_dist = e - s

    # 変な距離は防御
    if pl >= 0:
        if tp_dist <= 0:
            return 0.0
        return _clip(pl / tp_dist, -1.0, 1.0)
    else:
        if sl_dist <= 0:
            return 0.0
        return _clip(pl / sl_dist, -1.0, 1.0)


def _set_model_field_if_exists(obj: Any, field: str, value: Any) -> bool:
    """
    VirtualTrade にフィールドが存在するときだけセットする（存在しなければ何もしない）
    """
    try:
        names = {f.name for f in obj._meta.get_fields()}  # type: ignore[attr-defined]
        if field in names:
            setattr(obj, field, value)
            return True
    except Exception:
        pass
    return False


class Command(BaseCommand):
    help = "AIシミュレログに結果（PL / ラベル / exit情報）を付与 + EV_true(-1..1)確定 + Rank付与 + VirtualTrade同期"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価に使う営業日数",
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
            self.stdout.write(self.style.WARNING(f"[ai_sim_eval] dir not found: {sim_dir}"))
            return

        self.stdout.write(f"[ai_sim_eval] dir={sim_dir} horizon_days={horizon_days} force={force}")

        total = 0
        evaluated = 0
        db_updated = 0
        db_missed = 0

        for path in sorted(sim_dir.glob("*.jsonl")):
            self.stdout.write(f"  読み込み中: {path.name}")

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    読み込み失敗: {e}"))
                continue

            # 1) まず全行を dict 化（壊れた行は raw のまま保持）
            parsed: List[Tuple[bool, Any]] = []  # (is_dict, payload)
            for line in lines:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec: Dict[str, Any] = json.loads(raw)
                    parsed.append((True, rec))
                except Exception:
                    parsed.append((False, raw))

            # 2) 評価 & EV_true 確定（dict行のみ）
            updated_dicts: List[Dict[str, Any]] = []
            for is_dict, payload in parsed:
                if not is_dict:
                    continue
                rec = payload
                total += 1

                already_has_eval = (
                    "eval_label_rakuten" in rec
                    or "eval_label_matsui" in rec
                    or "eval_close_px" in rec
                    or "eval_exit_reason" in rec
                )
                if already_has_eval and not force:
                    updated = rec
                else:
                    _ensure_trade_date(rec)
                    try:
                        updated = eval_sim_record(rec, horizon_days=horizon_days)
                    except Exception as e:
                        code = rec.get("code")
                        ts = rec.get("ts")
                        self.stdout.write(self.style.ERROR(f"    評価エラー: {e} (file={path.name}, code={code}, ts={ts})"))
                        updated = rec
                    evaluated += 1

                # ---- EV_true（実現）を確定してレコードに入れる ----
                entry_px = _safe_float(updated.get("eval_entry_px") or updated.get("entry"))
                exit_px = _safe_float(updated.get("eval_close_px") or updated.get("eval_exit_px"))
                tp_px = _safe_float(updated.get("tp"))
                sl_px = _safe_float(updated.get("sl"))
                exit_reason = str(updated.get("eval_exit_reason") or "")

                ev_true_common = _calc_ev_true_from_eval(
                    entry_px=entry_px,
                    exit_px=exit_px,
                    tp_px=tp_px,
                    sl_px=sl_px,
                    exit_reason=exit_reason,
                )

                # 口座別：qty=0 / label=no_position は 0 に寄せる（学習/表示の事故防止）
                for k_qty, k_lbl, k_ev in (
                    ("qty_rakuten", "eval_label_rakuten", "ev_true_rakuten"),
                    ("qty_matsui", "eval_label_matsui", "ev_true_matsui"),
                    ("qty_sbi", "eval_label_sbi", "ev_true_sbi"),
                ):
                    qty = _safe_int(updated.get(k_qty) or 0) or 0
                    lbl = str(updated.get(k_lbl) or "").lower()
                    if qty <= 0 or lbl == "no_position":
                        updated[k_ev] = 0.0
                    else:
                        updated[k_ev] = float(ev_true_common) if ev_true_common is not None else None

                # PRO（C対応：今後はこれを主軸にできる）
                qty_pro = _safe_int(updated.get("qty_pro"))
                lbl_pro = str(updated.get("eval_label_pro") or "").lower()
                if qty_pro is not None:
                    if qty_pro <= 0 or lbl_pro == "no_position":
                        updated["ev_true_pro"] = 0.0
                    else:
                        updated["ev_true_pro"] = float(ev_true_common) if ev_true_common is not None else None

                # ステータス（Rank対象かどうか）
                #  no_position → SKIP / それ以外 → EVALUATED
                status = "EVALUATED"
                if str(exit_reason).lower() in ("no_position", "no_entry", "not_filled", "skip"):
                    status = "SKIP"
                updated["eval_status"] = status
                updated["evaluated_at"] = timezone.now().isoformat()

                updated_dicts.append(updated)

            # 3) Rank 付与（run_id + user_id 単位、EVALUATED だけ、EV_true_pro優先→無ければ楽天→松井→SBI）
            groups: DefaultDict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)

            for rec in updated_dicts:
                user_id = _safe_int(rec.get("user_id"))
                run_id = str(rec.get("run_id") or "").strip()
                if not user_id or not run_id:
                    continue
                if str(rec.get("eval_status") or "") != "EVALUATED":
                    continue
                groups[(run_id, int(user_id))].append(rec)

            def _rank_score(r: Dict[str, Any]) -> float:
                # PRO があれば PRO。なければ楽天→松井→SBI
                for key in ("ev_true_pro", "ev_true_rakuten", "ev_true_matsui", "ev_true_sbi"):
                    v = _safe_float(r.get(key))
                    if v is not None:
                        return float(v)
                return -999.0

            for (run_id, user_id), recs in groups.items():
                recs.sort(key=_rank_score, reverse=True)
                for i, r in enumerate(recs, start=1):
                    r["rank"] = i
                    r["rank_group"] = f"{run_id}:{user_id}"

            # 4) DB sync (CLOSE) + Rank/EV_true を replay に保存（フィールドがあれば直接も保存）
            #    ※壊れ行(raw文字列)も含めて元の順序で書き戻す
            new_lines: List[str] = []

            for is_dict, payload in parsed:
                if not is_dict:
                    new_lines.append(str(payload))
                    continue

                rec = payload

                # updated_dicts から同一キーを拾う（user_id+run_id+code）
                user_id = _safe_int(rec.get("user_id"))
                run_id = str(rec.get("run_id") or "")
                code = str(rec.get("code") or "")

                matched = None
                if user_id and run_id and code:
                    for u in updated_dicts:
                        if (
                            _safe_int(u.get("user_id")) == int(user_id)
                            and str(u.get("run_id") or "") == run_id
                            and str(u.get("code") or "") == code
                        ):
                            matched = u
                            break

                out_rec = matched if matched is not None else rec
                new_lines.append(json.dumps(out_rec, ensure_ascii=False))

                # ---- DB sync ----
                try:
                    if not (user_id and run_id and code):
                        db_missed += 1
                        continue

                    try:
                        vt = VirtualTrade.objects.get(user_id=int(user_id), run_id=str(run_id), code=str(code))
                    except VirtualTrade.DoesNotExist:
                        db_missed += 1
                        continue

                    vt.eval_horizon_days = out_rec.get("eval_horizon_days")

                    vt.eval_label_rakuten = str(out_rec.get("eval_label_rakuten") or "")
                    vt.eval_label_matsui = str(out_rec.get("eval_label_matsui") or "")
                    vt.eval_label_sbi = str(out_rec.get("eval_label_sbi") or "")

                    vt.eval_pl_rakuten = out_rec.get("eval_pl_rakuten")
                    vt.eval_pl_matsui = out_rec.get("eval_pl_matsui")
                    vt.eval_pl_sbi = out_rec.get("eval_pl_sbi")

                    vt.eval_exit_px = out_rec.get("eval_close_px")
                    vt.eval_exit_reason = str(out_rec.get("eval_exit_reason") or "")

                    vt.eval_entry_px = out_rec.get("eval_entry_px")

                    entry_ts = _parse_dt_iso(out_rec.get("eval_entry_ts"))
                    exit_ts = _parse_dt_iso(out_rec.get("eval_exit_ts"))
                    vt.eval_entry_ts = entry_ts
                    vt.eval_exit_ts = exit_ts

                    # “強制クローズ完了” の本体：closed_at を埋める
                    if exit_ts is not None and vt.closed_at is None:
                        vt.closed_at = exit_ts

                    # replay に保存（EV_true / rank / status も含める）
                    rp = vt.replay or {}
                    rp["last_eval"] = out_rec
                    rp["ev_true"] = {
                        "rakuten": out_rec.get("ev_true_rakuten"),
                        "matsui": out_rec.get("ev_true_matsui"),
                        "sbi": out_rec.get("ev_true_sbi"),
                        "pro": out_rec.get("ev_true_pro"),
                    }
                    rp["rank"] = out_rec.get("rank")
                    rp["eval_status"] = out_rec.get("eval_status")
                    vt.replay = rp

                    # フィールドが存在するなら直接保存（将来あなたが model に追加しても壊れない）
                    _set_model_field_if_exists(vt, "ev_true_rakuten", out_rec.get("ev_true_rakuten"))
                    _set_model_field_if_exists(vt, "ev_true_matsui", out_rec.get("ev_true_matsui"))
                    _set_model_field_if_exists(vt, "ev_true_sbi", out_rec.get("ev_true_sbi"))
                    _set_model_field_if_exists(vt, "ev_true_pro", out_rec.get("ev_true_pro"))
                    _set_model_field_if_exists(vt, "rank", out_rec.get("rank"))
                    _set_model_field_if_exists(vt, "eval_status", out_rec.get("eval_status"))
                    _set_model_field_if_exists(vt, "evaluated_at", _parse_dt_iso(out_rec.get("evaluated_at")))

                    # R を計算して保存（既存）
                    vt.recompute_r()

                    # update_fields（存在しないフィールド名は入れない）
                    update_fields = [
                        "eval_horizon_days",
                        "eval_label_rakuten", "eval_label_matsui", "eval_label_sbi",
                        "eval_pl_rakuten", "eval_pl_matsui", "eval_pl_sbi",
                        "eval_exit_px", "eval_exit_reason",
                        "eval_entry_px", "eval_entry_ts",
                        "eval_exit_ts", "closed_at",
                        "replay",
                        "result_r_rakuten", "result_r_sbi", "result_r_matsui",
                    ]

                    # 追加フィールドがある場合だけ入れる
                    for f in ("ev_true_rakuten", "ev_true_matsui", "ev_true_sbi", "ev_true_pro", "rank", "eval_status", "evaluated_at"):
                        try:
                            names = {ff.name for ff in vt._meta.get_fields()}
                            if f in names:
                                update_fields.append(f)
                        except Exception:
                            pass

                    vt.save(update_fields=update_fields)
                    db_updated += 1

                except Exception:
                    db_missed += 1
                    continue

            # 5) ファイル書き戻し（bakを残す）
            backup = path.with_suffix(path.suffix + ".bak")
            try:
                if backup.exists():
                    backup.unlink()
                path.replace(backup)
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                self.stdout.write(self.style.SUCCESS(f"  → 書き込み完了: {path.name} (backup: {backup.name})"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  書き込み失敗: {e}"))
                if backup.exists() and not path.exists():
                    backup.replace(path)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[ai_sim_eval] 全レコード: {total} / 評価: {evaluated}"))
        self.stdout.write(self.style.SUCCESS(f"[ai_sim_eval] DB更新: {db_updated} / DB取りこぼし: {db_missed}"))
        self.stdout.write(self.style.SUCCESS("[ai_sim_eval] 完了"))