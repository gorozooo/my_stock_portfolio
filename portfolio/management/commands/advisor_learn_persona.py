# -*- coding: utf-8 -*-
"""
管理コマンド: advisor_learn_persona（堅牢化版）

目的:
- LINEの feedback.jsonl を重み付きで集計し、
  few-shot用の gorozooo_examples.jsonl に反映。
- 集計サマリを persona_stats.json に保存。
- 排他ロック・バックアップ・壊れ行スキップ・文字正規化・
  アトミック書き込みなどを備えた安定運用向け実装。

使い方:
  venv/bin/python manage.py advisor_learn_persona
  # 例: 23:40に平日だけ実行（cron）
  # 40 23 * * 1-5  cd $BASE && /usr/bin/flock -n $LOG/learn_persona.lock \
  #   $PY manage.py advisor_learn_persona >> $LOG/learn_persona.log 2>&1
"""
from __future__ import annotations
import os, io, json, json as _json, hashlib, math, unicodedata
import datetime as dt
import errno
from typing import Any, Dict, Iterable, List, Optional, Tuple, DefaultDict
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings

# =============================
# パス & I/O ユーティリティ
# =============================

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.path.join(os.getcwd(), "media")

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _paths() -> Dict[str, str]:
    base = os.path.join(_media_root(), "advisor")
    _ensure_dir(base)
    return {
        "feedback": os.path.join(base, "feedback.jsonl"),
        "examples": os.path.join(base, "gorozooo_examples.jsonl"),
        "stats":    os.path.join(base, "persona_stats.json"),
        "state":    os.path.join(base, "learn_state.json"),
        "lock":     os.path.join(base, ".learn_persona.lock"),
        "backup_dir": os.path.join(base, "_backup"),
    }

def _open_atomic(path: str):
    """
    書き込み: 一時ファイルに出力 → fsync → rename
    with を使う側で .write して close すればOK
    """
    _ensure_dir(os.path.dirname(path))
    tmp = f"{path}.tmp"
    return open(tmp, "w", encoding="utf-8")

def _commit_atomic(path: str) -> None:
    tmp = f"{path}.tmp"
    if not os.path.exists(tmp):
        return
    # fsync
    with open(tmp, "r+", encoding="utf-8") as f:
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def _backup_file(src: str, backup_dir: str, keep: int) -> None:
    if keep <= 0 or not os.path.exists(src):
        return
    _ensure_dir(backup_dir)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(src)
    dst = os.path.join(backup_dir, f"{base}.{ts}.bak")
    try:
        # 軽量コピー（同一FS前提）: 読み→書き
        with open(src, "rb") as fr, open(dst, "wb") as fw:
            while True:
                buf = fr.read(1024 * 1024)
                if not buf: break
                fw.write(buf)
        # 古いバックアップ整理
        files = sorted(
            [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.startswith(base + ".")],
            key=os.path.getmtime,
            reverse=True,
        )
        for old in files[keep:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        # バックアップ失敗は致命ではないので握りつぶす
        pass


# =============================
# JSONL 読み書き（堅牢）
# =============================

_MAX_LINE_BYTES = 64 * 1024  # 64KB/行の安全上限（過剰行はスキップ）

def _read_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    bad = 0
    with io.open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            try:
                if not raw:
                    continue
                if len(raw.encode("utf-8", "ignore")) > _MAX_LINE_BYTES:
                    bad += 1
                    continue
                line = raw.strip()
                if not line:
                    continue
                yield json.loads(line)
            except Exception:
                bad += 1
                continue
    if bad:
        # 最後にまとめて通知（呼び出し側ログに出る）
        yield from []  # generator消費のための no-op


def _write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    with _open_atomic(path) as f:
        for r in rows:
            f.write(_json.dumps(r, ensure_ascii=False))
            f.write("\n")
    _commit_atomic(path)

def _write_json(path: str, obj: Any) -> None:
    with _open_atomic(path) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    _commit_atomic(path)

def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# =============================
# 正規化・雑関数
# =============================

def _norm_mode(m: Optional[str]) -> str:
    m = (m or "").strip().lower()
    return m if m in ("preopen","postopen","noon","afternoon","outlook") else "generic"

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")

def _parse_ts(ts: Optional[str]) -> dt.datetime:
    if not ts:
        return dt.datetime.now(dt.timezone.utc)
    try:
        return dt.datetime.fromisoformat(ts.replace("Z","+00:00"))
    except Exception:
        return dt.datetime.now(dt.timezone.utc)

def _mk_key(mode: str, text: str) -> str:
    h = hashlib.sha1((mode + "||" + text).encode("utf-8")).hexdigest()
    return f"{mode}:{h}"

def _coerce_list(x: Any) -> List[Any]:
    if x is None: return []
    if isinstance(x, list): return x
    return [x]

def _normalize_text(text: str, limit: int = 280) -> str:
    """全角半角のゆらぎ吸収 + 余分な空白除去 + 長さ制限"""
    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = " ".join(t.split())
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


# =============================
# 重み・減衰
# =============================

CHOICE_WEIGHT = {
    "up": 1.0, "good": 1.0, "👍": 1.0, "like": 1.0, "ok": 1.0,
    "edit": 0.3, "fix": 0.3, "修正": 0.3, "✏️": 0.3,
    "down": -0.7, "bad": -0.7, "👎": -0.7, "ng": -0.7, "no": -0.7,
}

HALF_LIFE_DAYS = 30.0
N_MAX_TOTAL    = 1200
N_PER_MODE     = 250
WEIGHT_MIN, WEIGHT_MAX = -2.5, 6.0

def _time_decay_factor(then: dt.datetime, now: Optional[dt.datetime] = None) -> float:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    days = (now - then).total_seconds() / 86400.0
    if days <= 0:
        return 1.0
    return 0.5 ** (days / HALF_LIFE_DAYS)

def _base_weight_of(rec: Dict[str, Any]) -> float:
    if "weight" in rec:
        try:
            w = float(rec["weight"])
            if math.isnan(w) or math.isinf(w):
                return 0.0
            return max(WEIGHT_MIN, min(WEIGHT_MAX, w))
        except Exception:
            pass
    ch = str(rec.get("choice", "")).strip().lower()
    return CHOICE_WEIGHT.get(ch, 0.0)

def _effective_text(rec: Dict[str, Any]) -> Optional[str]:
    choice = str(rec.get("choice", "")).lower()
    if choice in ("edit","fix","修正","✏️") and rec.get("edited_text"):
        return _normalize_text(str(rec["edited_text"]))
    t = rec.get("text")
    return _normalize_text(str(t)) if isinstance(t, str) else None

def _infer_tone_tags(text: str) -> List[str]:
    tags: List[str] = []
    s = text
    if any(k in s for k in ["買いが優勢","強気","買い寄り","底堅"]):
        tags.append("tone:buy")
    if any(k in s for k in ["売りが優勢","慎重","警戒","売り寄り"]):
        tags.append("tone:sell")
    if "拮抗" in s or "様子見" in s or "静かな" in s:
        tags.append("tone:flat")
    for kw, t in [
        ("半導体","sec:半導体"), ("生成AI","sec:AI"), ("素材","sec:素材"),
        ("エネルギー","sec:エネルギー"), ("金融","sec:金融"),
        ("ディフェンシブ","sec:ディフェンシブ"), ("インフラ","sec:インフラ"),
    ]:
        if kw in s:
            tags.append(t)
    # unique
    seen = set()
    out: List[str] = []
    for x in tags:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def _load_existing_examples(path: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(path):
        try:
            text = row.get("text")
            mode = _norm_mode(row.get("mode"))
            if not isinstance(text, str) or not text.strip():
                continue
            text = _normalize_text(text)
            key = row.get("key") or _mk_key(mode, text)
            w = float(row.get("weight", 0.0))
            if math.isnan(w) or math.isinf(w):
                w = 0.0
            out[key] = {
                "key": key,
                "mode": mode,
                "text": text,
                "weight": max(WEIGHT_MIN, min(WEIGHT_MAX, w)),
                "ts_first": row.get("ts_first") or row.get("ts") or _now_iso(),
                "ts_last":  row.get("ts_last")  or row.get("ts") or _now_iso(),
                "count":    int(row.get("count", 1)),
                "tags":     list(_coerce_list(row.get("tags"))),
                "notes":    row.get("notes") or "",
            }
        except Exception:
            continue
    return out


# =============================
# 集計ロジック
# =============================

def _aggregate_feedback(
    feedback_path: str,
    existing: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any], int]:
    now = dt.datetime.now(dt.timezone.utc)
    stats: Dict[str, Any] = dict(
        total=0, up=0, down=0, edit=0,
        added=0, updated=0, skipped=0,
        by_mode=defaultdict(int),  # type: ignore
        weight_sum=0.0,
    )

    bad_lines = 0

    for rec in _read_jsonl(feedback_path):
        # _read_jsonl 側でも壊れ行は握りつぶしているが、
        # 念のため各レコードでも型防御
        try:
            stats["total"] += 1
            mode = _norm_mode(rec.get("mode"))
            stats["by_mode"][mode] += 1

            base_w = _base_weight_of(rec)
            choice = str(rec.get("choice", "")).lower()
            if choice in ("up","good","👍","like","ok"):   stats["up"] += 1
            elif choice in ("down","bad","👎","ng","no"):  stats["down"] += 1
            elif choice in ("edit","fix","修正","✏️"):     stats["edit"] += 1

            txt = _effective_text(rec)
            if not txt:
                stats["skipped"] += 1
                continue

            key = _mk_key(mode, txt)
            tstamp = _parse_ts(rec.get("ts"))
            decay = _time_decay_factor(tstamp, now)
            eff_w = max(WEIGHT_MIN, min(WEIGHT_MAX, base_w * decay))
            stats["weight_sum"] += eff_w

            tags = set(_coerce_list(rec.get("tags")))
            for t in _infer_tone_tags(txt):
                tags.add(t)

            if key in existing:
                row = existing[key]
                last_dt = _parse_ts(row.get("ts_last"))
                row_decay = _time_decay_factor(last_dt, now)
                new_w = float(row.get("weight", 0.0)) * row_decay + eff_w
                row["weight"] = max(WEIGHT_MIN, min(WEIGHT_MAX, new_w))
                row["ts_last"] = tstamp.isoformat()
                row["count"] = int(row.get("count", 1)) + 1
                row["tags"] = sorted(set(row.get("tags", [])) | tags)
                stats["updated"] += 1
            else:
                existing[key] = dict(
                    key=key, mode=mode, text=txt, weight=eff_w,
                    ts_first=tstamp.isoformat(), ts_last=tstamp.isoformat(),
                    count=1, tags=sorted(tags), notes=rec.get("comment") or "",
                )
                stats["added"] += 1

        except Exception:
            bad_lines += 1
            continue

    # クリーニング
    for k, row in list(existing.items()):
        w = float(row.get("weight", 0.0))
        if math.isnan(w) or math.isinf(w):
            row["weight"] = 0.0
        else:
            row["weight"] = max(WEIGHT_MIN, min(WEIGHT_MAX, w))

    return existing, _freeze_stats(stats), bad_lines

def _freeze_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    by_mode = {k: int(v) for k, v in getattr(stats["by_mode"], "items", lambda: [])()}
    out = dict(stats)
    out["by_mode"] = by_mode
    return out

def _prune_examples(examples: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_mode: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in examples.values():
        by_mode[row["mode"]].append(row)

    kept: List[Dict[str, Any]] = []
    for mode, rows in by_mode.items():
        rows.sort(key=lambda r: r["weight"], reverse=True)
        top_pos = [r for r in rows if r["weight"] > 0]
        negs    = [r for r in rows if r["weight"] <= 0]
        take_pos = top_pos[: int(N_PER_MODE * 0.9)]
        take_neg = negs[: max(5, int(N_PER_MODE * 0.1))]
        kept.extend((take_pos + take_neg)[:N_PER_MODE])

    kept.sort(key=lambda r: r["weight"], reverse=True)
    if len(kept) > N_MAX_TOTAL:
        kept = kept[:N_MAX_TOTAL]
    return kept


# =============================
# ロック（単純・安全）
# =============================

class SimplePidLock:
    """PIDを書いた排他ロック。古いロックは max_age 秒で自動破棄。"""
    def __init__(self, path: str, max_age: int = 7200):
        self.path = path
        self.max_age = max_age
        _ensure_dir(os.path.dirname(path))
        self.acquired = False

    def acquire(self) -> bool:
        now = dt.datetime.now().timestamp()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()},{int(now)}\n")
            self.acquired = True
            return True
        except OSError as e:
            if e.errno != errno.EEXIST:
                return False
            # 既存ロックの鮮度確認
            try:
                with open(self.path, "r") as f:
                    line = f.read().strip()
                parts = line.split(",")
                ts = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                if now - ts > self.max_age:
                    # ステールロック破棄
                    os.remove(self.path)
                    return self.acquire()
            except Exception:
                try:
                    os.remove(self.path)
                except Exception:
                    pass
            return False

    def release(self) -> None:
        if self.acquired:
            try:
                os.remove(self.path)
            except Exception:
                pass
            self.acquired = False


# =============================
# コマンド本体
# =============================

class Command(BaseCommand):
    help = "LINEフィードバック(feedback.jsonl)を重み付きで学習し、few-shot用コーパス(gorozooo_examples.jsonl)を更新します。"

    def add_arguments(self, parser: CommandParser) -> None:
        p = _paths()
        parser.add_argument("--feedback", type=str, default=p["feedback"], help="入力: フィードバックjsonl")
        parser.add_argument("--examples", type=str, default=p["examples"], help="出力: 例文jsonl")
        parser.add_argument("--stats",    type=str, default=p["stats"],    help="出力: 統計json")
        parser.add_argument("--state",    type=str, default=p["state"],    help="出力: 学習状態")
        parser.add_argument("--lock-file", type=str, default=p["lock"],    help="排他ロックファイル")
        parser.add_argument("--backup-keep", type=int, default=5,          help="各出力のバックアップ保持数（0で無効）")
        parser.add_argument("--half-life-days", type=float, default=HALF_LIFE_DAYS, help="重み半減期(日)")
        parser.add_argument("--max-total", type=int, default=N_MAX_TOTAL,  help="全体の最大件数")
        parser.add_argument("--per-mode",  type=int, default=N_PER_MODE,   help="モード別の最大件数")
        parser.add_argument("--dry-run",   action="store_true",            help="出力を書き込まずログのみ")

    def handle(self, *args, **opts):
        global HALF_LIFE_DAYS, N_MAX_TOTAL, N_PER_MODE
        HALF_LIFE_DAYS = float(opts["half_life_days"])
        N_MAX_TOTAL    = int(opts["max_total"])
        N_PER_MODE     = int(opts["per_mode"])

        feedback_path = opts["feedback"]
        examples_path = opts["examples"]
        stats_path    = opts["stats"]
        state_path    = opts["state"]
        lock_path     = opts["lock_file"]
        backup_keep   = int(opts["backup_keep"])
        dry_run       = bool(opts["dry_run"])
        backup_dir    = _paths()["backup_dir"]

        self.stdout.write(self.style.HTTP_INFO(f"[learn] feedback={feedback_path}"))
        self.stdout.write(self.style.HTTP_INFO(f"[learn] examples={examples_path}"))
        self.stdout.write(self.style.HTTP_INFO(f"[learn] stats={stats_path}"))
        self.stdout.write(self.style.HTTP_INFO(f"[learn] lock={lock_path} dry_run={dry_run}"))

        # ---- 排他ロック ----
        lock = SimplePidLock(lock_path, max_age=2*60*60)
        if not lock.acquire():
            return self.stdout.write(self.style.WARNING("[lock] another process is running; skip."))

        try:
            # 既存コーパス読込
            existing = _load_existing_examples(examples_path)
            n_before = len(existing)
            self.stdout.write(self.style.NOTICE(f"[load] examples: {n_before} rows"))

            # フィードバック集計
            updated, stats, bad_lines = _aggregate_feedback(feedback_path, existing)
            self.stdout.write(self.style.NOTICE(
                "[aggregate] total={total} up={up} edit={edit} down={down} "
                "added={added} updated={updated} skipped={skipped} weight_sum={weight_sum:.2f}"
                .format(**stats)
            ))
            if bad_lines:
                self.stdout.write(self.style.WARNING(f"[aggregate] malformed lines skipped: {bad_lines}"))

            # プルーニング
            pruned = _prune_examples(updated)
            n_after = len(pruned)
            self.stdout.write(self.style.NOTICE(f"[prune] -> keep {n_after} rows (before {n_before})"))

            # トーンの重み合計（参考）
            tone_w = dict(buy=0.0, sell=0.0, flat=0.0)
            for r in pruned:
                w = float(r.get("weight", 0.0))
                tags = set(r.get("tags", []))
                if "tone:buy" in tags:  tone_w["buy"]  += w
                if "tone:sell" in tags: tone_w["sell"] += w
                if "tone:flat" in tags: tone_w["flat"] += w

            # 出力
            if dry_run:
                self.stdout.write(self.style.SUCCESS("[done] dry-run; no files written."))
                return

            # バックアップ
            _backup_file(examples_path, backup_dir, backup_keep)
            _backup_file(stats_path, backup_dir, backup_keep)
            _backup_file(state_path, backup_dir, backup_keep)

            # 書き込み（アトミック）
            _write_jsonl(examples_path, pruned)
            _write_json(stats_path, dict(
                updated_at=_now_iso(),
                total_examples=n_after,
                **stats,
                tone_weight=tone_w,
                params=dict(half_life_days=HALF_LIFE_DAYS, max_total=N_MAX_TOTAL, per_mode=N_PER_MODE),
            ))
            _write_json(state_path, dict(
                updated_at=_now_iso(),
                examples_path=examples_path,
                feedback_path=feedback_path,
                last_total_feedback=stats["total"],
                kept_examples=n_after,
            ))

            self.stdout.write(self.style.SUCCESS("[done] persona examples updated."))
        finally:
            lock.release()