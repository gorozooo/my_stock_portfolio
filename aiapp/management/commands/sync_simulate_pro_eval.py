# aiapp/management/commands/sync_simulate_pro_eval.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


# =========================
# utils
# =========================
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


def _safe_str(v: Any) -> str:
    if v in (None, "", "null"):
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _parse_date(v: Any) -> Optional[date]:
    """
    "YYYY-MM-DD" / date / datetime を date にする
    """
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = _safe_str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


def _iter_jsonl_lines(path: Path) -> Tuple[List[str], List[Optional[Dict[str, Any]]]]:
    """
    生行配列と、パースできた JSON dict（できないなら None）を返す
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return [], []
    parsed: List[Optional[Dict[str, Any]]] = []
    for line in lines:
        s = line.strip()
        if not s:
            parsed.append(None)
            continue
        try:
            parsed.append(json.loads(s))
        except Exception:
            parsed.append(None)
    return lines, parsed


# =========================
# matching
# =========================
@dataclass
class EvalPack:
    user_id: int
    trade_date: date
    code: str
    qty_pro: float
    label: str
    exit_reason: str
    entry_px: Optional[float]
    exit_px: Optional[float]
    pl_pro: Optional[float]
    pl_per_share: Optional[float]
    horizon_bd: Optional[int]
    run_id: str


def _get_last_eval_pro(v: VirtualTrade) -> Optional[Dict[str, Any]]:
    pro = ((v.replay or {}).get("pro") or {})
    le = (pro.get("last_eval") or {})
    if not isinstance(le, dict) or not le:
        return None
    return le


def _build_eval_pack(v: VirtualTrade) -> Optional[EvalPack]:
    le = _get_last_eval_pro(v)
    if le is None:
        return None

    td = _parse_date(getattr(v, "trade_date", None) or le.get("trade_date"))
    if td is None:
        return None

    code = _safe_str(getattr(v, "code", "")).strip()
    if not code:
        code = _safe_str(le.get("code")).strip()
    if not code:
        return None

    label = _safe_str(le.get("label")).lower().strip()
    if not label:
        return None

    qty = _safe_float(le.get("qty_pro"))
    if qty is None:
        qty = _safe_float(((v.replay or {}).get("pro") or {}).get("qty_pro"))
    if qty is None or qty <= 0:
        return None

    run_id = _safe_str(getattr(v, "run_id", "")).strip()

    return EvalPack(
        user_id=int(getattr(v, "user_id", 0) or 0),
        trade_date=td,
        code=code,
        qty_pro=float(qty),
        label=label,
        exit_reason=_safe_str(le.get("exit_reason")).lower().strip(),
        entry_px=_safe_float(le.get("entry_px")),
        exit_px=_safe_float(le.get("exit_px")),
        pl_pro=_safe_float(le.get("pl_pro")),
        pl_per_share=_safe_float(le.get("pl_per_share")),
        horizon_bd=_safe_int(le.get("horizon_bd")),
        run_id=run_id,
    )


def _score_candidate(rec: Dict[str, Any], ep: EvalPack) -> float:
    """
    候補が複数ある場合の「どれを採用するか」スコア（小さいほど良い）
    - entry がズレるのは仕様 → 近い方を選ぶだけ
    """
    s = 0.0
    entry = _safe_float(rec.get("entry"))
    if entry is not None and ep.entry_px is not None:
        s += abs(entry - ep.entry_px) / max(1.0, abs(ep.entry_px))
    else:
        s += 1.0

    rid = _safe_str(rec.get("run_id")).strip()
    if rid and ep.run_id and rid == ep.run_id:
        s -= 0.5

    pd = _parse_date(rec.get("price_date"))
    if pd is not None and pd == ep.trade_date:
        s -= 0.1

    return s


def _compute_eval_pl_pro(rec: Dict[str, Any], ep: EvalPack) -> Optional[float]:
    """
    last_eval の pl_pro を最優先で simulate に書き戻す
    """
    if ep.pl_pro is not None:
        return float(ep.pl_pro)

    # 保険：pl_per_share * qty_pro
    if ep.pl_per_share is not None:
        qty = _safe_float(rec.get("qty_pro"))
        if qty is None:
            qty = ep.qty_pro
        try:
            return float(ep.pl_per_share) * float(qty)
        except Exception:
            return None

    return None


def _compute_eval_r_pro(rec: Dict[str, Any], eval_pl: Optional[float]) -> Optional[float]:
    """
    R を作る（任意だけどモデルが生きる）
    優先：est_loss_pro
    次点：abs(entry - sl) * qty_pro
    """
    if eval_pl is None:
        return None

    try:
        est_loss = _safe_float(rec.get("est_loss_pro"))
        if est_loss is not None and float(est_loss) != 0.0:
            return float(eval_pl) / abs(float(est_loss))
    except Exception:
        pass

    try:
        entry = _safe_float(rec.get("entry"))
        sl = _safe_float(rec.get("sl"))
        qty = _safe_float(rec.get("qty_pro"))
        if entry is None or sl is None or qty is None:
            return None
        risk = abs(float(entry) - float(sl)) * float(qty)
        if risk <= 0:
            return None
        return float(eval_pl) / risk
    except Exception:
        return None


# =========================
# command
# =========================
class Command(BaseCommand):
    help = "VirtualTrade(replay.pro.last_eval) を simulate JSONL に書き戻し（PRO一択）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=10, help="直近何日分の trade_date を対象にする")
        parser.add_argument("--user", type=int, default=None, help="対象ユーザーID（省略時は全ユーザー）")
        parser.add_argument("--date-max", type=str, default=None, help="上限日（YYYY-MM-DD）。省略時は今日")
        parser.add_argument("--limit", type=int, default=0, help="対象 vtrade を最大何件に制限（0=無制限）")
        parser.add_argument("--dry-run", action="store_true", help="書き込みを行わずログだけ出す")

    def handle(self, *args, **options) -> None:
        days: int = int(options["days"])
        user_id: Optional[int] = options.get("user")
        date_max_s: Optional[str] = options.get("date_max")
        limit: int = int(options.get("limit") or 0)
        dry_run: bool = bool(options.get("dry_run"))
        verbosity: int = int(options.get("verbosity") or 1)

        if date_max_s:
            dm = _parse_date(date_max_s)
            date_max = dm if dm is not None else timezone.localdate()
        else:
            date_max = timezone.localdate()

        date_min = date_max - timedelta(days=max(0, days - 1))

        simulate_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        self.stdout.write(
            f"[sync_simulate_pro_eval] start days={days} limit={limit} user={user_id} "
            f"date_max={date_max.isoformat()} dry_run={dry_run}"
        )

        if not simulate_dir.exists():
            self.stdout.write(self.style.WARNING("[sync_simulate_pro_eval] simulate_dir not found"))
            return

        # 対象 vtrade 抽出
        qs = VirtualTrade.objects.all()
        qs = qs.filter(trade_date__gte=date_min, trade_date__lte=date_max)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        qs = qs.order_by("-trade_date", "-id")
        if limit and limit > 0:
            qs = qs[:limit]

        packs: List[EvalPack] = []
        skipped_no_last_eval = 0
        skipped_no_qty_pro = 0

        for v in qs:
            ep = _build_eval_pack(v)
            if ep is None:
                le = _get_last_eval_pro(v)
                if le is None:
                    skipped_no_last_eval += 1
                else:
                    skipped_no_qty_pro += 1
                continue
            packs.append(ep)

        target_vtrades = len(packs)
        touched_run_ids: List[str] = sorted({p.run_id for p in packs if p.run_id})

        scanned_files = 0
        scanned_lines = 0
        parsed_records = 0
        matched_ep = 0
        updated_records = 0
        updated_files = 0

        # packが何回当たったか（重複カウント防止）
        matched_keys: set[Tuple[int, str, str, int]] = set()

        # scan 対象：orders系を中心に処理
        paths = sorted(simulate_dir.glob("*.jsonl"))

        for path in paths:
            if not path.name.startswith("sim_orders_"):
                continue

            scanned_files += 1
            raw_lines, parsed = _iter_jsonl_lines(path)
            if not raw_lines:
                continue

            scanned_lines += len(raw_lines)

            # index: (user, code, trade_date, mode, qty_int) -> [(line_idx, rec)]
            idx: Dict[Tuple[int, str, date, str, int], List[Tuple[int, Dict[str, Any]]]] = {}

            for i, rec in enumerate(parsed):
                if rec is None:
                    continue
                parsed_records += 1

                uid = _safe_int(rec.get("user_id")) or 0
                if user_id is not None and uid != user_id:
                    continue

                code = _safe_str(rec.get("code")).strip()
                td = _parse_date(rec.get("trade_date"))
                mode = _safe_str(rec.get("mode")).lower().strip() or "other"
                qty = _safe_float(rec.get("qty_pro")) or 0.0

                if not code or td is None or qty <= 0:
                    continue

                key = (uid, code, td, mode, int(qty))
                idx.setdefault(key, []).append((i, rec))

            file_changed = False

            for ep in packs:
                # 既にこのep（キー）が当たってるなら飛ばす
                ep_key = (ep.user_id, ep.code, ep.trade_date.isoformat(), int(ep.qty_pro))
                if ep_key in matched_keys:
                    continue

                found = False

                for mode in ("demo", "live", "other"):
                    key = (ep.user_id, ep.code, ep.trade_date, mode, int(ep.qty_pro))
                    cands = idx.get(key) or []
                    if not cands:
                        continue

                    best_i = None
                    best_rec = None
                    best_score = 1e18
                    for (li, rec) in cands:
                        sc = _score_candidate(rec, ep)
                        if sc < best_score:
                            best_score = sc
                            best_i = li
                            best_rec = rec

                    if best_i is None or best_rec is None:
                        continue

                    # ---- 書き戻し ----
                    label = ep.label
                    exit_reason = ep.exit_reason
                    eval_pl = _compute_eval_pl_pro(best_rec, ep)
                    eval_r = _compute_eval_r_pro(best_rec, eval_pl)

                    if ep.horizon_bd is not None:
                        best_rec["eval_horizon_days"] = int(ep.horizon_bd)

                    best_rec["eval_label_pro"] = label
                    best_rec["eval_exit_reason_pro"] = exit_reason

                    if eval_pl is not None:
                        best_rec["eval_pl_pro"] = float(eval_pl)
                    if eval_r is not None:
                        best_rec["eval_r_pro"] = float(eval_r)

                    if ep.exit_px is not None:
                        best_rec["eval_exit_px_pro"] = float(ep.exit_px)
                    if ep.entry_px is not None:
                        best_rec["eval_entry_px_pro"] = float(ep.entry_px)

                    parsed[best_i] = best_rec
                    file_changed = True
                    updated_records += 1

                    matched_keys.add(ep_key)
                    matched_ep += 1
                    found = True

                    if verbosity >= 2:
                        self.stdout.write(
                            f"[sync_simulate_pro_eval] update file={path.name} line={best_i} "
                            f"code={ep.code} date={ep.trade_date.isoformat()} qty={int(ep.qty_pro)} label={label}"
                        )

                    break  # mode loop
                if found:
                    continue  # 次のepへ

            if file_changed:
                if not dry_run:
                    out_lines: List[str] = []
                    for i, orig in enumerate(raw_lines):
                        rec = parsed[i] if i < len(parsed) else None
                        if rec is None:
                            out_lines.append(orig)
                            continue
                        out_lines.append(json.dumps(rec, ensure_ascii=False))
                    _atomic_write_text(path, "\n".join(out_lines) + "\n")
                updated_files += 1

        skipped_no_match = max(0, target_vtrades - matched_ep)

        self.stdout.write("")
        self.stdout.write("===== sync_simulate_pro_eval summary =====")
        self.stdout.write(f"  scanned_files       : {scanned_files}")
        self.stdout.write(f"  scanned_lines       : {scanned_lines}")
        self.stdout.write(f"  parsed_records      : {parsed_records}")
        self.stdout.write(f"  target_vtrades      : {target_vtrades}")
        self.stdout.write(f"  matched             : {matched_ep}")
        self.stdout.write(f"  updated_records     : {updated_records}")
        self.stdout.write(f"  updated_files       : {updated_files}")
        self.stdout.write(f"  skipped_no_last_eval: {skipped_no_last_eval}")
        self.stdout.write(f"  skipped_no_qty_pro  : {skipped_no_qty_pro}")
        self.stdout.write(f"  skipped_no_match    : {skipped_no_match}")
        self.stdout.write(f"  touched_run_ids     : {len(touched_run_ids)}")
        if touched_run_ids:
            self.stdout.write("  run_ids:")
            for rid in touched_run_ids:
                self.stdout.write(f"    - {rid}")

        self.stdout.write("[sync_simulate_pro_eval] done")