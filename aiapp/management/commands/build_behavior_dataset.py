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
    ※ PRO一択：broker は常に "pro"
    """
    user_id: int
    ts: str
    mode: str           # "live" / "demo" / "other"
    broker: str         # "pro"
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

    # ---- 追加：表示＆学習の“原因” ----
    entry_reason: Optional[str]

    # ---- 追加：ML実数値（ログが入っていれば表示できる）----
    p_win: Optional[float]
    p_tp_first: Optional[float]
    p_sl_first: Optional[float]
    ev_pred: Optional[float]
    ev_true: Optional[float]

    entry_k: Optional[float]
    rr_target: Optional[float]
    tp_k: Optional[float]
    sl_k: Optional[float]

    # ---- 任意：監査用（あっても害はない）----
    ml_ok: Optional[bool]
    ml_reason: Optional[str]

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

            # 追加
            "entry_reason": self.entry_reason,

            # 追加（ML）
            "p_win": self.p_win,
            "p_tp_first": self.p_tp_first,
            "p_sl_first": self.p_sl_first,
            "ev_pred": self.ev_pred,
            "ev_true": self.ev_true,
            "entry_k": self.entry_k,
            "rr_target": self.rr_target,
            "tp_k": self.tp_k,
            "sl_k": self.sl_k,

            # 監査
            "ml_ok": self.ml_ok,
            "ml_reason": self.ml_reason,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except Exception:
        return None


def _safe_str(v: Any) -> Optional[str]:
    if v in (None, "", "null"):
        return None
    s = str(v).strip()
    return s if s else None


def _safe_bool(v: Any) -> Optional[bool]:
    if v in (None, "", "null"):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return None


def _get_nested(d: Dict[str, Any], path: str) -> Any:
    """
    path例: "replay.pro.entry_reason"
    """
    cur: Any = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _pick_first(d: Dict[str, Any], keys: List[str], nested_paths: List[str] = []) -> Any:
    for k in keys:
        if k in d and d.get(k) not in (None, "", "null"):
            return d.get(k)
    for p in nested_paths:
        v = _get_nested(d, p)
        if v not in (None, "", "null"):
            return v
    return None


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


def _price_date_from_record(r: Dict[str, Any]) -> Optional[str]:
    # ai_simulate_auto の主流：trade_date / run_date
    v = r.get("price_date") or r.get("trade_date") or r.get("run_date")
    return _safe_str(v)


def _dedup_records(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    シミュレレコードの重複除外（PRO一択）。

    キー：
      (user_id, mode, code, price_date, entry[小数3桁丸め], qty_pro)

    → 同じキーのものは 1件にまとめる。
    ※ 同一キーが複数ある場合、ts が新しいものを優先する。
    """
    best: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

    def key_of(r: Dict[str, Any]) -> Tuple[Any, ...]:
        entry = _safe_float(r.get("entry")) or 0.0
        price_date = _price_date_from_record(r)
        return (
            r.get("user_id"),
            (r.get("mode") or "").lower(),
            r.get("code"),
            price_date,
            round(entry, 3),
            _safe_float(r.get("qty_pro")) or 0.0,
        )

    # ts 降順（ISO文字列なら概ねこれでOK）
    rows_sorted = sorted(raw_rows, key=lambda x: str(x.get("ts") or ""), reverse=True)

    for r in rows_sorted:
        k = key_of(r)
        if k not in best:
            best[k] = r

    out = list(best.values())
    out.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    return out


class Command(BaseCommand):
    """
    /media/aiapp/simulate/sim_orders_*.jsonl を読み込み、
    - 重複シミュレを除外した「行動データセット」
    - 学習用の「1トレード×PRO」データセット
    を /media/aiapp/behavior/ 配下に出力する。

    出力ファイル：
      - YYYYMMDD_behavior_dataset.jsonl
      - latest_behavior.jsonl
      - YYYYMMDD_behavior_side.jsonl
      - latest_behavior_side.jsonl
    """

    help = "AI シミュレログから行動データセット／学習用データセットを構築する（PRO一択）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価対象の horizon_days（主にログ用）",
        )
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（指定なしなら全ユーザー）",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = int(options["days"])
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
        # ★重要：sim_orders だけ読む（他のjsonl混入で先頭が古くなるのを防ぐ）
        raw_rows: List[Dict[str, Any]] = []
        files = sorted(simulate_dir.glob("sim_orders_*.jsonl"))
        file_count = 0

        for path in files:
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

                # price_date を補完（dedupキー安定化）
                if rec.get("price_date") in (None, "", "null"):
                    pd = _price_date_from_record(rec)
                    if pd:
                        rec["price_date"] = pd

                raw_rows.append(rec)

        if not raw_rows:
            self.stdout.write(self.style.WARNING("  対象レコードがありませんでした。"))
            return

        self.stdout.write(f"  読み込みファイル数: {file_count} / 行数: {len(raw_rows)}")

        # ---------- 重複除外（PRO基準） + 最新が先頭 ----------
        rows = _dedup_records(raw_rows)
        self.stdout.write(
            f"  重複除外後レコード数: {len(rows)} (差分: {len(raw_rows) - len(rows)})"
        )

        # ---------- 行動データセット（そのままの構造） ----------
        # ★最新が先頭になるように ts 降順で書く
        rows_sorted = sorted(rows, key=lambda x: str(x.get("ts") or ""), reverse=True)
        dataset_lines: List[str] = [json.dumps(r, ensure_ascii=False) for r in rows_sorted]

        today_str = timezone.localdate().strftime("%Y%m%d")
        dataset_path = behavior_dir / f"{today_str}_behavior_dataset.jsonl"
        latest_path = behavior_dir / "latest_behavior.jsonl"

        dataset_text = "\n".join(dataset_lines) + ("\n" if dataset_lines else "")
        _atomic_write_text(dataset_path, dataset_text)
        _atomic_write_text(latest_path, dataset_text)

        self.stdout.write(
            self.style.SUCCESS(
                f"  行動データセットを書き出しました: {dataset_path.name} (件数: {len(rows_sorted)})"
            )
        )
        self.stdout.write(self.style.SUCCESS(f"  latest_behavior.jsonl を更新しました（{len(rows_sorted)} 件）"))

        # ---------- 学習用 Side データセット（PRO一択） ----------
        side_rows: List[SideRow] = []

        for r in rows_sorted:
            user_id = int(r.get("user_id") or 0)
            ts = str(r.get("ts") or "")
            mode = (str(r.get("mode") or "") or "").lower() or "other"
            if mode not in ("live", "demo"):
                mode = "other"

            price_date = _price_date_from_record(r)

            base_kwargs = dict(
                user_id=user_id,
                ts=ts,
                mode=mode,
                code=str(r.get("code") or ""),
                name=str(r.get("name") or ""),
                sector=(r.get("sector") or None),
                price_date=price_date,
                entry=_safe_float(r.get("entry")),
                tp=_safe_float(r.get("tp")),
                sl=_safe_float(r.get("sl")),
                eval_horizon_days=_safe_int(r.get("eval_horizon_days")),
                # sim_orders 側は atr で来ることが多い
                atr_14=_safe_float(r.get("atr_14")) if r.get("atr_14") is not None else _safe_float(r.get("atr")),
                slope_20=_safe_float(r.get("slope_20")),
                trend_daily=(r.get("trend_daily") or None),
            )

            side = self._build_side_row_pro(r, base_kwargs=base_kwargs)
            if side is not None:
                side_rows.append(side)

        side_lines = [json.dumps(sr.to_dict(), ensure_ascii=False) for sr in side_rows]

        side_path = behavior_dir / f"{today_str}_behavior_side.jsonl"
        latest_side_path = behavior_dir / "latest_behavior_side.jsonl"

        side_text = "\n".join(side_lines) + ("\n" if side_lines else "")
        _atomic_write_text(side_path, side_text)
        _atomic_write_text(latest_side_path, side_text)

        self.stdout.write(
            self.style.SUCCESS(
                f"  学習用データセットを書き出しました: {side_path.name} (件数: {len(side_rows)})"
            )
        )
        self.stdout.write(self.style.SUCCESS(f"  latest_behavior_side.jsonl を更新しました（{len(side_rows)} 件）"))
        self.stdout.write(self.style.SUCCESS("[build_behavior_dataset] 完了"))

    # =========================================================
    # サイド行（PRO）の構築
    # =========================================================

    def _build_side_row_pro(
        self,
        r: Dict[str, Any],
        base_kwargs: Dict[str, Any],
    ) -> Optional[SideRow]:
        """
        1つのシミュレレコードから PRO の SideRow を作る（PRO一択）。

        ✅ PROキーが無いなら学習データにしない。

        - qty_pro <= 0 → 学習に使わない
        - eval_label(_pro) が win/lose/flat 以外 → 学習に使わない
        - eval_pl(_pro) が無い → 学習に使わない

        ★対応（今回）:
        - eval_* は _pro だけでなく、eval_pl / eval_label / eval_r も拾う
        - entry_reason は top-level / replay.sim_order / replay.pro から拾う
        - p_sl_first も拾って保存する
        """
        qty = _safe_float(r.get("qty_pro")) or 0.0
        if qty <= 0:
            return None

        eval_label = (r.get("eval_label_pro") or r.get("eval_label") or "").lower()
        if eval_label not in ("win", "lose", "flat"):
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

        # ---------------------------
        # entry_reason（できるだけ拾う）
        # ---------------------------
        entry_reason = _pick_first(
            r,
            keys=[
                "entry_reason",
                "entry_reason_pro",
            ],
            nested_paths=[
                # ai_simulate_auto: replay.sim_order.entry_reason が入る
                "replay.sim_order.entry_reason",
                # run_common_pro_meta が replay.pro 内に入る
                "replay.pro.entry_reason",
                # 念のため
                "replay.entry_reason",
                "meta.entry_reason",
            ],
        )
        entry_reason = _safe_str(entry_reason)

        # ---------------------------
        # ML 実数値（ログが入っていれば拾う）
        # ---------------------------
        p_win = _pick_first(
            r,
            keys=["p_win", "p_win_pro", "ml_p_win", "pwin"],
            nested_paths=["ml.p_win", "ml.pwin", "replay.ml.p_win", "replay.meta.p_win", "replay.sim_order.p_win"],
        )
        p_tp_first = _pick_first(
            r,
            keys=["p_tp_first", "p_tp_first_pro", "ml_p_tp_first", "ptp_first"],
            nested_paths=["ml.p_tp_first", "replay.ml.p_tp_first", "replay.meta.p_tp_first", "replay.sim_order.p_tp_first"],
        )
        p_sl_first = _pick_first(
            r,
            keys=["p_sl_first", "p_sl_first_pro", "ml_p_sl_first", "psl_first"],
            nested_paths=["ml.p_sl_first", "replay.ml.p_sl_first", "replay.meta.p_sl_first", "replay.sim_order.p_sl_first"],
        )
        ev_pred = _pick_first(
            r,
            keys=["ev_pred", "ev_pred_pro", "ml_ev_pred", "EV_pred"],
            nested_paths=["ml.ev_pred", "replay.ml.ev_pred", "replay.meta.ev_pred", "replay.sim_order.ev_pred"],
        )
        ev_true = _pick_first(
            r,
            keys=["ev_true", "ev_true_pro", "ml_ev_true", "EV_true"],
            nested_paths=["ml.ev_true", "replay.ml.ev_true", "replay.meta.ev_true", "replay.sim_order.ev_true"],
        )

        entry_k = _pick_first(
            r,
            keys=["entry_k", "entry_k_pro", "ml_entry_k"],
            nested_paths=["ml.entry_k", "replay.ml.entry_k", "replay.meta.entry_k", "replay.sim_order.entry_k"],
        )
        rr_target = _pick_first(
            r,
            keys=["rr_target", "rr_target_pro", "ml_rr_target", "rr"],
            nested_paths=["ml.rr_target", "replay.ml.rr_target", "replay.meta.rr_target", "replay.sim_order.rr_target"],
        )
        tp_k = _pick_first(
            r,
            keys=["tp_k", "tp_k_pro", "ml_tp_k"],
            nested_paths=["ml.tp_k", "replay.ml.tp_k", "replay.meta.tp_k", "replay.sim_order.tp_k"],
        )
        sl_k = _pick_first(
            r,
            keys=["sl_k", "sl_k_pro", "ml_sl_k"],
            nested_paths=["ml.sl_k", "replay.ml.sl_k", "replay.meta.sl_k", "replay.sim_order.sl_k"],
        )

        ml_ok = _pick_first(
            r,
            keys=["ml_ok", "ml_ok_pro"],
            nested_paths=["replay.sim_order.ml_ok", "replay.pro.ml_ok", "replay.ml.ok"],
        )
        ml_reason = _pick_first(
            r,
            keys=["ml_reason", "ml_reason_pro"],
            nested_paths=["replay.sim_order.ml_reason", "replay.pro.ml_reason", "replay.ml.reason"],
        )

        return SideRow(
            broker="pro",
            qty=float(qty),
            est_pl=est_pl,
            est_loss=est_loss,
            eval_pl=float(eval_pl),
            eval_r=eval_r,
            eval_label=eval_label,

            entry_reason=entry_reason,

            p_win=_safe_float(p_win),
            p_tp_first=_safe_float(p_tp_first),
            p_sl_first=_safe_float(p_sl_first),
            ev_pred=_safe_float(ev_pred),
            ev_true=_safe_float(ev_true),
            entry_k=_safe_float(entry_k),
            rr_target=_safe_float(rr_target),
            tp_k=_safe_float(tp_k),
            sl_k=_safe_float(sl_k),

            ml_ok=_safe_bool(ml_ok),
            ml_reason=_safe_str(ml_reason),

            **base_kwargs,
        )