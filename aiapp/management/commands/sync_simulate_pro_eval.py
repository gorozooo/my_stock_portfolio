# aiapp/management/commands/sync_simulate_pro_eval.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as _date, timedelta as _timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


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
    if v is None:
        return ""
    return str(v)


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


# ------------------------------------------------------------
# ファイル名から日付を抜く（sim_orders_YYYY-MM-DD.jsonl など）
# ------------------------------------------------------------
_DATE_RE = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")


def _date_from_filename(name: str) -> Optional[_date]:
    m = _DATE_RE.search(name)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _date(y, mo, d)
    except Exception:
        return None


def _norm_mode(v: Any) -> str:
    s = _safe_str(v).strip().lower()
    if s in ("live", "demo"):
        return s
    return "other"


def _norm_code(v: Any) -> str:
    # 1543 みたいなコードは文字列統一
    s = _safe_str(v).strip()
    return s


def _norm_date_str(v: Any) -> str:
    # "2025-12-23" など
    s = _safe_str(v).strip()
    return s


def _record_trade_key_date(r: Dict[str, Any]) -> str:
    """
    ✅ 王道(A-2)：
    price_date が無い（None/""）なら trade_date を使う。
    trade_date も無いなら run_date を使う。
    """
    pd = _norm_date_str(r.get("price_date"))
    if pd:
        return pd
    td = _norm_date_str(r.get("trade_date"))
    if td:
        return td
    rd = _norm_date_str(r.get("run_date"))
    return rd


def _extract_pro_last_eval(v: VirtualTrade) -> Optional[Dict[str, Any]]:
    """
    VirtualTrade.replay['pro']['last_eval'] を取り出す。
    """
    rep = v.replay or {}
    pro = rep.get("pro") or {}
    le = pro.get("last_eval") or {}
    if not isinstance(le, dict) or not le:
        return None
    return le


@dataclass
class VNeed:
    v_id: int
    user_id: int
    code: str
    trade_date: str
    mode: str
    qty_pro: float
    entry_px: Optional[float]
    label: str
    pl_yen: Optional[float]
    r_val: Optional[float]
    exit_reason: Optional[str]
    horizon_days: Optional[int]


def _build_vneed(v: VirtualTrade) -> Optional[VNeed]:
    le = _extract_pro_last_eval(v)
    if le is None:
        return None

    qty = _safe_float(le.get("qty_pro"))
    if qty is None or qty <= 0:
        return None

    code = _norm_code(getattr(v, "code", None))
    if not code:
        return None

    trade_date = getattr(v, "trade_date", None)
    if trade_date is None:
        return None
    trade_date_str = str(trade_date)

    mode = _norm_mode(getattr(v, "mode", None))

    label = _safe_str(le.get("label")).strip().lower()
    if not label:
        return None

    # last_eval 側のキーは揺れる可能性があるので複数候補で拾う
    entry_px = _safe_float(le.get("entry_px"))
    pl_yen = _safe_float(le.get("pl_yen"))
    if pl_yen is None:
        pl_yen = _safe_float(le.get("pl"))
    if pl_yen is None:
        pl_yen = _safe_float(le.get("pnl_yen"))

    r_val = _safe_float(le.get("r"))
    if r_val is None:
        r_val = _safe_float(le.get("r_val"))

    exit_reason = le.get("exit_reason")
    if exit_reason is None:
        exit_reason = le.get("reason")

    horizon_days = _safe_int(le.get("horizon_days"))
    if horizon_days is None:
        horizon_days = _safe_int(le.get("eval_horizon_days"))

    return VNeed(
        v_id=int(v.id),
        user_id=int(getattr(v, "user_id", 0) or 0),
        code=code,
        trade_date=trade_date_str,
        mode=mode,
        qty_pro=float(qty),
        entry_px=entry_px,
        label=label,
        pl_yen=pl_yen,
        r_val=r_val,
        exit_reason=_safe_str(exit_reason) if exit_reason is not None else None,
        horizon_days=horizon_days,
    )


def _is_json_line(s: str) -> bool:
    s = s.strip()
    return bool(s) and (s[0] == "{" and s[-1] == "}")


def _best_match_index(
    candidates: List[Tuple[int, Dict[str, Any]]],
    want_entry_px: Optional[float],
) -> Optional[int]:
    """
    candidates: [(line_index, record), ...]
    ✅ entry は一致不要。いちばん近いものを採用（王道）。
    """
    if not candidates:
        return None

    if want_entry_px is None:
        # entry比較できないなら先頭
        return candidates[0][0]

    best_i: Optional[int] = None
    best_d: Optional[float] = None

    for idx, r in candidates:
        e = _safe_float(r.get("entry"))
        if e is None:
            # entry無いのは後回し
            d = 10**18
        else:
            d = abs(e - want_entry_px)

        if best_d is None or d < best_d:
            best_d = d
            best_i = idx

    return best_i


class Command(BaseCommand):
    """
    ✅ 王道(A-2)での同期

    - VirtualTrade(replay.pro.last_eval) の PRO評価を、
      media/aiapp/simulate/*.jsonl の該当レコードへ書き戻す。
    - 突合は「price_dateが無いならtrade_dateを使う」「entryは近いもの採用」。

    更新するキー：
      eval_label_pro
      eval_pl_pro
      eval_r_pro
      eval_exit_reason_pro
      eval_horizon_days
      price_date（空なら trade_date を補完して埋める）
    """

    help = "VirtualTradeのPRO評価(last_eval)をsimulate JSONLへ同期（王道A-2: price_date補完 + entry近似）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=10, help="対象日数（trade_date 기준）")
        parser.add_argument("--user", type=int, default=None, help="対象ユーザーID（省略=全ユーザー）")
        parser.add_argument("--date-max", type=str, default=None, help="上限日(YYYY-MM-DD)（省略=今日）")
        parser.add_argument("--dry-run", action="store_true", help="書き込みせずログのみ")
        parser.add_argument("--limit", type=int, default=0, help="処理するVirtualTrade上限（0=無制限）")

    def handle(self, *args, **opts) -> None:
        days: int = int(opts["days"])
        user_id: Optional[int] = opts.get("user")
        date_max_str: Optional[str] = opts.get("date_max")
        dry_run: bool = bool(opts.get("dry_run"))
        verbosity: int = int(opts.get("verbosity") or 1)
        limit: int = int(opts.get("limit") or 0)

        if date_max_str:
            try:
                date_max = _date.fromisoformat(date_max_str)
            except Exception:
                date_max = timezone.localdate()
        else:
            date_max = timezone.localdate()

        date_min = date_max - _timedelta(days=max(0, days - 1))

        simulate_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not simulate_dir.exists():
            self.stdout.write(self.style.WARNING(f"[sync_simulate_pro_eval] simulate_dir not found: {simulate_dir}"))
            return

        self.stdout.write(
            f"[sync_simulate_pro_eval] start days={days} limit={limit} user={user_id} date_max={date_max} dry_run={dry_run}"
        )

        # ------------------------------------------------------------
        # 1) 対象 VirtualTrade を集める（trade_dateで絞る）
        # ------------------------------------------------------------
        qs = VirtualTrade.objects.all()
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        qs = qs.filter(trade_date__gte=date_min, trade_date__lte=date_max).order_by("-trade_date", "-id")
        if limit > 0:
            qs = qs[:limit]

        vneeds: List[VNeed] = []
        skipped_no_last_eval = 0
        for v in qs:
            vn = _build_vneed(v)
            if vn is None:
                skipped_no_last_eval += 1
                continue
            vneeds.append(vn)

        # ------------------------------------------------------------
        # 2) 対象ファイルを集める（ファイル名日付でざっくり絞る）
        # ------------------------------------------------------------
        all_files = sorted(simulate_dir.glob("*.jsonl"))
        target_files: List[Path] = []
        for p in all_files:
            d = _date_from_filename(p.name)
            if d is None:
                # 日付読めないものは一応入れる（ただし空ファイルもあるので後で弾く）
                target_files.append(p)
                continue
            if date_min <= d <= date_max:
                target_files.append(p)

        scanned_files = 0
        scanned_lines = 0
        parsed_records = 0

        matched = 0
        updated_records = 0
        updated_files = 0
        skipped_no_qty_pro = 0
        skipped_no_match = 0

        # どのファイルが更新されたか
        dirty_files: Dict[Path, List[str]] = {}

        # ------------------------------------------------------------
        # 3) まず全ファイルを読み込み、検索用インデックスを作る
        #    index_key = (user_id, code, date_key, mode, qty_pro_intish)
        # ------------------------------------------------------------
        # file_lines_map[path] = list[str] (raw lines)
        file_lines_map: Dict[Path, List[str]] = {}
        # file_rec_map[path] = list[Optional[dict]]  (JSONならdict、ダメならNone)
        file_rec_map: Dict[Path, List[Optional[Dict[str, Any]]]] = {}
        # global index: key -> list[(path, line_idx, rec)]
        index: Dict[Tuple[int, str, str, str, int], List[Tuple[Path, int, Dict[str, Any]]]] = {}

        for path in target_files:
            scanned_files += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                lines = []
            file_lines_map[path] = lines
            recs: List[Optional[Dict[str, Any]]] = []
            file_rec_map[path] = recs

            for i, line in enumerate(lines):
                scanned_lines += 1
                s = line.strip()
                if not s:
                    recs.append(None)
                    continue
                if not _is_json_line(s):
                    recs.append(None)
                    continue
                try:
                    rec = json.loads(s)
                except Exception:
                    recs.append(None)
                    continue

                parsed_records += 1
                recs.append(rec)

                # userフィルタ（index作成時点で減らす）
                ru = rec.get("user_id")
                if ru is None:
                    continue
                if user_id is not None and int(ru) != int(user_id):
                    continue

                qty = _safe_float(rec.get("qty_pro")) or 0.0
                if qty <= 0:
                    continue

                code = _norm_code(rec.get("code"))
                mode = _norm_mode(rec.get("mode"))
                date_key = _record_trade_key_date(rec)  # price_date補完
                if not code or not date_key:
                    continue

                # qtyは float揺れがあるので int丸めでキー化（11, 100, 700 みたいな前提）
                qty_key = int(round(qty))

                k = (int(ru), code, date_key, mode, qty_key)
                index.setdefault(k, []).append((path, i, rec))

        # ------------------------------------------------------------
        # 4) VirtualTradeごとに simulate レコードを探して書き戻す
        # ------------------------------------------------------------
        touched_run_ids: set[str] = set()

        for vn in vneeds:
            if vn.qty_pro <= 0:
                skipped_no_qty_pro += 1
                continue

            qty_key = int(round(vn.qty_pro))
            k = (vn.user_id, vn.code, vn.trade_date, vn.mode, qty_key)
            cands = index.get(k) or []

            if not cands:
                skipped_no_match += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"[sync_simulate_pro_eval] no_match v_id={vn.v_id} user={vn.user_id} "
                        f"code={vn.code} trade_date={vn.trade_date} mode={vn.mode} qty_pro={qty_key}"
                    )
                continue

            # best candidate by closest entry to entry_px (approx)
            best_line_idx: Optional[int] = _best_match_index(
                candidates=[(line_idx, rec) for (_p, line_idx, rec) in cands],
                want_entry_px=vn.entry_px,
            )
            if best_line_idx is None:
                skipped_no_match += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"[sync_simulate_pro_eval] no_match(best_none) v_id={vn.v_id} user={vn.user_id} code={vn.code}"
                    )
                continue

            # candidates may be across multiple files; pick the one with that line index in first matching path
            chosen: Optional[Tuple[Path, int, Dict[str, Any]]] = None
            for (p, li, rec) in cands:
                if li == best_line_idx:
                    chosen = (p, li, rec)
                    break
            if chosen is None:
                chosen = cands[0]

            path, li, rec = chosen

            # 既に同じ評価が入ってるならスキップ（余計な上書きを避ける）
            cur_label = (rec.get("eval_label_pro") or "")
            cur_pl = rec.get("eval_pl_pro")
            cur_r = rec.get("eval_r_pro")

            new_label = vn.label
            # carry/skip/no_position もそのまま入れてよい（学習側で弾く）
            new_pl = vn.pl_yen if vn.pl_yen is not None else 0.0
            new_r = vn.r_val

            # price_date が空なら trade_date を補完して埋める（A-2の肝）
            if not _norm_date_str(rec.get("price_date")):
                rec["price_date"] = vn.trade_date

            rec["eval_label_pro"] = new_label
            rec["eval_pl_pro"] = float(new_pl)
            if new_r is None:
                # 無ければキー自体を消さず None を入れる（後段でsafeに処理できる）
                rec["eval_r_pro"] = None
            else:
                rec["eval_r_pro"] = float(new_r)

            if vn.exit_reason is not None and vn.exit_reason != "":
                rec["eval_exit_reason_pro"] = vn.exit_reason

            if vn.horizon_days is not None:
                rec["eval_horizon_days"] = int(vn.horizon_days)

            # run_id を収集
            rid = rec.get("run_id")
            if rid:
                touched_run_ids.add(str(rid))

            # 変更があったときだけdirtyへ
            changed = (cur_label != rec.get("eval_label_pro")) or (cur_pl != rec.get("eval_pl_pro")) or (cur_r != rec.get("eval_r_pro"))
            if changed:
                matched += 1
                updated_records += 1

                # rec を行へ戻す
                new_line = json.dumps(rec, ensure_ascii=False)
                lines = file_lines_map.get(path) or []
                if 0 <= li < len(lines):
                    lines[li] = new_line
                    file_lines_map[path] = lines
                    dirty_files[path] = lines

                if verbosity >= 2:
                    self.stdout.write(
                        f"[sync_simulate_pro_eval] update file={path.name} line={li} "
                        f"code={vn.code} date={vn.trade_date} qty={qty_key} label={new_label}"
                    )
            else:
                matched += 1  # 見つかったが更新不要

        # ------------------------------------------------------------
        # 5) 書き戻し
        # ------------------------------------------------------------
        if not dry_run:
            for path, lines in dirty_files.items():
                text = "\n".join(lines) + ("\n" if lines else "")
                _atomic_write_text(path, text)
                updated_files += 1

        self.stdout.write("")
        self.stdout.write("===== sync_simulate_pro_eval summary =====")
        self.stdout.write(f"  scanned_files       : {scanned_files}")
        self.stdout.write(f"  scanned_lines       : {scanned_lines}")
        self.stdout.write(f"  parsed_records      : {parsed_records}")
        self.stdout.write(f"  target_vtrades      : {len(vneeds)}")
        self.stdout.write(f"  matched             : {matched}")
        self.stdout.write(f"  updated_records     : {updated_records}")
        self.stdout.write(f"  updated_files       : {updated_files}")
        self.stdout.write(f"  skipped_no_last_eval: {skipped_no_last_eval}")
        self.stdout.write(f"  skipped_no_qty_pro  : {skipped_no_qty_pro}")
        self.stdout.write(f"  skipped_no_match    : {skipped_no_match}")
        self.stdout.write(f"  touched_run_ids     : {len(touched_run_ids)}")
        if verbosity >= 2 and touched_run_ids:
            self.stdout.write("  run_ids:")
            for x in sorted(touched_run_ids):
                self.stdout.write(f"    - {x}")

        self.stdout.write("[sync_simulate_pro_eval] done")