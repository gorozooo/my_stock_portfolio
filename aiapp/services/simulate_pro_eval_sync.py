# aiapp/services/simulate_pro_eval_sync.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


# =========================================================
# utilities
# =========================================================

def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except Exception:
        return None


def _norm_code(code: Any) -> str:
    s = str(code or "").strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _norm_mode(mode: Any) -> str:
    s = str(mode or "").strip().lower()
    if s in ("live", "demo"):
        return s
    return "other"


def _norm_date_str(v: Any) -> str:
    """
    simulate側は price_date（YYYY-MM-DD）が基本。
    無ければ trade_date 等が混ざることもあるので、先頭10桁だけ使う。
    """
    if v is None:
        return ""
    s = str(v).strip()
    if len(s) >= 10:
        return s[:10]
    return s


def _round_entry(v: Any) -> float:
    x = _safe_float(v)
    if x is None:
        return 0.0
    return round(float(x), 3)


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


def _iter_jsonl(path: Path) -> Iterable[Tuple[int, str]]:
    """
    JSONLを「(行番号, 生行文字列)」で返す。空行も返す（原文維持のため）。
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(True)  # keepends
    except Exception:
        return []
    for i, raw in enumerate(lines):
        yield i, raw


def _parse_json_line(line: str) -> Optional[Dict[str, Any]]:
    s = (line or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
        return None
    except Exception:
        return None


def _dump_json_line(obj: Dict[str, Any], *, ensure_ascii: bool = False) -> str:
    return json.dumps(obj, ensure_ascii=ensure_ascii)


def _make_sim_key(
    rec: Dict[str, Any],
    *,
    use_mode: bool = True,
) -> Tuple[Any, ...]:
    """
    simulate JSONL の行を一意に寄せるキー。
    基本は build_behavior_dataset の dedup と同じ思想で作る。

    キー（推奨）:
      (user_id, mode, code, price_date, entry_round, qty_pro)
    mode を外したキーも併用（VirtualTrade側にmodeが無い/ズレてるケースに備える）
    """
    user_id = rec.get("user_id")
    mode = _norm_mode(rec.get("mode")) if use_mode else ""
    code = _norm_code(rec.get("code"))
    price_date = _norm_date_str(rec.get("price_date") or rec.get("trade_date") or rec.get("run_date"))
    entry = _round_entry(rec.get("entry"))
    qty = _safe_float(rec.get("qty_pro")) or 0.0
    qty = float(qty)
    if use_mode:
        return (user_id, mode, code, price_date, entry, qty)
    return (user_id, code, price_date, entry, qty)


def _extract_r_from_meta(meta: Any) -> Optional[float]:
    """
    ai_sim_eval の meta から R を拾う（A案の r_plan を優先）。
    meta:
      {"A": {"r_plan": ...}, "exit": {...}} の想定
    """
    if not isinstance(meta, dict):
        return None
    a = meta.get("A")
    if isinstance(a, dict):
        r = _safe_float(a.get("r_plan"))
        if r is not None:
            return float(r)
    return None


@dataclass
class SyncStats:
    scanned_files: int = 0
    scanned_lines: int = 0
    parsed_records: int = 0

    target_vtrades: int = 0
    matched: int = 0
    updated_records: int = 0
    updated_files: int = 0

    skipped_no_last_eval: int = 0
    skipped_no_qty_pro: int = 0
    skipped_no_match: int = 0


def sync_simulate_pro_eval(
    *,
    days: int = 10,
    limit: int = 0,
    user_id: Optional[int] = None,
    date_max: Optional[str] = None,
    dry_run: bool = False,
    verbose: int = 1,
) -> SyncStats:
    """
    王道A-2：
    - VirtualTrade（PRO公式）で確定した評価（replay["pro"]["last_eval"]）を
    - simulate JSONL（media/aiapp/simulate/*.jsonl）へ書き戻す。

    目的:
      build_behavior_dataset が eval_*_pro を拾える状態にする（side=0件を解消）

    書き戻すキー:
      eval_label_pro / eval_pl_pro / eval_r_pro / eval_horizon_days / qty_pro（不足時補完）
    """
    stats = SyncStats()

    simulate_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
    if not simulate_dir.exists():
        return stats

    # -------------------------------------------
    # 1) simulate を読み、キー→(file, line_idx, rec) の索引を作る
    # -------------------------------------------
    index_full: Dict[Tuple[Any, ...], List[Tuple[Path, int, Dict[str, Any]]]] = {}
    index_nomode: Dict[Tuple[Any, ...], List[Tuple[Path, int, Dict[str, Any]]]] = {}

    files = sorted(simulate_dir.glob("*.jsonl"))
    stats.scanned_files = len(files)

    # fileごとの「生行」保持（更新が必要なfileだけ最後に書き戻す）
    file_lines_map: Dict[Path, List[str]] = {}
    file_dirty: Dict[Path, bool] = {}

    for fp in files:
        # まず全行を保持（原文維持のため）
        try:
            raw_lines = fp.read_text(encoding="utf-8", errors="replace").splitlines(True)  # keepends
        except Exception:
            continue

        file_lines_map[fp] = raw_lines
        file_dirty[fp] = False

        for i, raw in enumerate(raw_lines):
            stats.scanned_lines += 1
            rec = _parse_json_line(raw)
            if rec is None:
                continue
            stats.parsed_records += 1

            # user フィルタ（simulate側で削れる）
            if user_id is not None and rec.get("user_id") != user_id:
                continue

            k_full = _make_sim_key(rec, use_mode=True)
            k_nm = _make_sim_key(rec, use_mode=False)
            index_full.setdefault(k_full, []).append((fp, i, rec))
            index_nomode.setdefault(k_nm, []).append((fp, i, rec))

    # -------------------------------------------
    # 2) VirtualTrade（PRO公式）を対象抽出
    # -------------------------------------------
    now_local = timezone.localtime()
    today = now_local.date()

    # date_max を明示されたら優先（YYYY-MM-DD）
    if date_max:
        try:
            dmax = timezone.datetime.fromisoformat(str(date_max)[:10]).date()
        except Exception:
            dmax = today
    else:
        dmax = today

    if days <= 0:
        dmin = dmax
    else:
        dmin = dmax - timezone.timedelta(days=int(days))

    qs = VirtualTrade.objects.filter(
        trade_date__gte=dmin,
        trade_date__lte=dmax,
    ).filter(
        Q(replay__pro__status="accepted") | Q(replay__pro__enabled=True)
    )

    if user_id is not None:
        # VirtualTrade に user が無い設計の可能性もあるので try
        try:
            qs = qs.filter(user_id=user_id)
        except Exception:
            pass

    qs = qs.order_by("-opened_at", "-id")
    if limit and limit > 0:
        qs = qs[:limit]

    targets = list(qs)
    stats.target_vtrades = len(targets)

    # -------------------------------------------
    # 3) 1件ずつ、simulate側をマッチさせて eval_*_pro を書き戻す
    # -------------------------------------------
    for v in targets:
        replay = v.replay if isinstance(v.replay, dict) else {}
        pro = replay.get("pro") if isinstance(replay.get("pro"), dict) else {}
        last_eval = pro.get("last_eval") if isinstance(pro.get("last_eval"), dict) else None

        if not isinstance(last_eval, dict):
            stats.skipped_no_last_eval += 1
            continue

        qty_pro = _safe_int(last_eval.get("qty_pro"))
        if qty_pro is None:
            qty_pro = _safe_int(getattr(v, "qty_pro", None))
        if qty_pro is None or qty_pro <= 0:
            stats.skipped_no_qty_pro += 1
            continue

        # simulate 側のキー材料
        v_user_id = getattr(v, "user_id", None)
        v_code = _norm_code(getattr(v, "code", ""))
        v_price_date = str(getattr(v, "trade_date", "") or "")
        if len(v_price_date) >= 10:
            v_price_date = v_price_date[:10]

        entry_px = last_eval.get("entry_px")
        if entry_px is None:
            entry_px = getattr(v, "eval_entry_px", None) or getattr(v, "entry_px", None)
        entry_round = round(float(_safe_float(entry_px) or 0.0), 3)

        # mode は last_eval に入れてない想定なので、ここは「まずmodeあり」「ダメならmode無し」で探す
        # user_id も同様に None の可能性があるので、simulate側の user_id が入ってれば一致する。
        # ただ、あなたの運用は user_id が入ってる前提なので、それに乗る。
        mode_candidates = ["live", "demo", "other"]
        found: Optional[Tuple[Path, int, Dict[str, Any]]] = None

        # 1) modeありキーで探索
        for m in mode_candidates:
            k_full = (v_user_id, m, v_code, v_price_date, entry_round, float(qty_pro))
            lst = index_full.get(k_full)
            if lst:
                found = lst[0]
                break

        # 2) mode無しキーで探索
        if found is None:
            k_nm = (v_user_id, v_code, v_price_date, entry_round, float(qty_pro))
            lst = index_nomode.get(k_nm)
            if lst:
                found = lst[0]

        if found is None:
            stats.skipped_no_match += 1
            if verbose >= 2:
                # どのキーで探したか最低限残す
                print(
                    f"[sync_simulate_pro_eval] no_match "
                    f"v_id={v.id} user={v_user_id} code={v_code} price_date={v_price_date} entry={entry_round} qty_pro={qty_pro}"
                )
            continue

        fp, line_idx, rec = found
        stats.matched += 1

        # --- 書き戻す値（PRO公式） ---
        exit_reason = str(last_eval.get("exit_reason") or "").strip().lower()
        label = str(last_eval.get("label") or "").strip().lower()

        pl_pro = _safe_float(last_eval.get("pl_pro"))
        plps = _safe_float(last_eval.get("pl_per_share"))

        # eval_pl_pro は「円の損益」を優先（pl_pro）
        eval_pl_pro: Optional[float]
        if pl_pro is not None:
            eval_pl_pro = float(pl_pro)
        else:
            # fallback（数量×pl_per_share）
            if plps is not None:
                eval_pl_pro = float(plps) * float(qty_pro)
            else:
                eval_pl_pro = None

        meta = last_eval.get("meta")
        eval_r_pro = _extract_r_from_meta(meta)

        # 勝敗ラベルは win/lose/flat/no_position/carry/skip の可能性がある
        # build_behavior_dataset は win/lose/flat しか学習に使わない
        eval_label_pro = label

        # eval_horizon_days は last_eval の horizon_bd を使う（bd=営業日）
        eval_horizon_days = _safe_int(last_eval.get("horizon_bd"))
        if eval_horizon_days is None:
            eval_horizon_days = _safe_int(getattr(v, "eval_horizon_days", None))

        # --- rec に反映 ---
        changed = False

        def _set(k: str, val: Any) -> None:
            nonlocal changed
            cur = rec.get(k)
            if cur != val:
                rec[k] = val
                changed = True

        _set("qty_pro", int(qty_pro))
        _set("eval_label_pro", eval_label_pro)
        _set("eval_pl_pro", eval_pl_pro)
        _set("eval_r_pro", eval_r_pro)
        _set("eval_horizon_days", eval_horizon_days)

        # 参考：exit_reason も残しておくとデバッグしやすい（ページ側で見たいなら）
        _set("eval_exit_reason_pro", exit_reason)

        if not changed:
            continue

        stats.updated_records += 1

        if dry_run:
            continue

        # --- ファイルの該当行を差し替え（改行は維持） ---
        original_line = file_lines_map[fp][line_idx]
        newline = "\n" if original_line.endswith("\n") else ""
        file_lines_map[fp][line_idx] = _dump_json_line(rec, ensure_ascii=False) + newline
        file_dirty[fp] = True

    # -------------------------------------------
    # 4) dirty なファイルだけ書き戻す
    # -------------------------------------------
    if not dry_run:
        for fp, dirty in file_dirty.items():
            if not dirty:
                continue
            stats.updated_files += 1
            new_text = "".join(file_lines_map[fp])
            _atomic_write_text(fp, new_text, encoding="utf-8")

    return stats