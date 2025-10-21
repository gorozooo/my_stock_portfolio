# -*- coding: utf-8 -*-
"""
管理コマンド: advisor_learn_persona

目的:
- LINE からのフィードバック (feedback.jsonl) を読み取り、
  「良い例・悪い例・修正例」を重み付きで集計し、
  few-shot 用の例文コーパス (gorozooo_examples.jsonl) を自動更新する。
- 週/日次などで定期実行し、徐々に「gorozooo」人格のトーン/文体/温度感を育てる。
- 学習サマリ (persona_stats.json) を出力し、トレンドを可視化。

配置:
- ファイル名: portfolio/management/commands/advisor_learn_persona.py
- 依存: Django settings (MEDIA_ROOT), Python標準ライブラリのみ

I/O:
- 入力:  media/advisor/feedback.jsonl         # 1行=1イベント(JSON)。例は下記。
- 出力1: media/advisor/gorozooo_examples.jsonl # few-shot用コーパス(重み付き)
- 出力2: media/advisor/persona_stats.json      # 集計メタデータ（ダッシュボード用）

feedback.jsonl の例:
{"ts":"2025-10-21T07:21:10+09:00","mode":"preopen","choice":"up","text":"🔥買いが優勢…","tags":["tone:hot","sec:半導体"]}
{"ts":"2025-10-21T09:51:05+09:00","mode":"postopen","choice":"edit","text":"🌤拮抗…","edited_text":"🌤拮抗、薄利で回すのが吉。","weight":0.5}
{"ts":"2025-10-21T12:01:33+09:00","mode":"noon","choice":"down","text":"🌧売りが優勢…","comment":"少し悲観すぎ"}

重みルール（デフォルト）:
- choice == "up"/"good"/"👍"      -> +1.0
- choice == "edit"/"fix"/"✏️"    -> +0.3（edited_text があればそれを本文採用）
- choice == "down"/"bad"/"👎"    -> -0.7
- レコードに "weight" キーがあればそれを優先使用（正負どちらも可）
- 同一 (mode, text) キーは同一サンプルとして集約し、重みを加算
- 時間減衰: 半減期 half-life=30日。古い重みは W *= 0.5 ** (Δdays/30)

保持数:
- 全体最大 N_MAX_TOTAL = 1200
- モード別上限 N_PER_MODE = 250
- 下位（負の重みが大きいもの）は適度に残しつつも、学習用の上位サンプルを優先

使い方:
- 手動:   venv/bin/python manage.py advisor_learn_persona
- cron例: 40 23 * * 1-5  cd $BASE && /usr/bin/flock -n $LOG/learn_persona.lock \
           $PY manage.py advisor_learn_persona >> $LOG/learn_persona.log 2>&1
"""
from __future__ import annotations
import os, json, json as _json, hashlib, math, datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Tuple, DefaultDict
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings


# ====== パス関連 =============================================================

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
    }


# ====== 読み書きユーティリティ =============================================

def _read_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def _write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(_json.dumps(r, ensure_ascii=False))
            f.write("\n")
    os.replace(tmp, path)

def _write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ====== モデル化: サンプル行定義 ============================================

def _norm_mode(m: Optional[str]) -> str:
    m = (m or "").strip().lower()
    return m if m in ("preopen","postopen","noon","afternoon","outlook") else "generic"

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")

def _parse_ts(ts: Optional[str]) -> dt.datetime:
    # なるべく頑強に
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

# choice -> default weight
CHOICE_WEIGHT = {
    "up":     1.0, "good": 1.0, "👍": 1.0, "like": 1.0, "ok": 1.0,
    "edit":   0.3, "fix": 0.3, "修正": 0.3, "✏️": 0.3,
    "down":  -0.7, "bad": -0.7, "👎": -0.7, "ng": -0.7, "no": -0.7,
}

HALF_LIFE_DAYS = 30.0  # 半減期（任意）
N_MAX_TOTAL    = 1200
N_PER_MODE     = 250

def _time_decay_factor(then: dt.datetime, now: Optional[dt.datetime] = None) -> float:
    """半減期に基づく指数減衰係数を返す"""
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    # ensure timezone aware
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    days = (now - then).total_seconds() / 86400.0
    if days <= 0:
        return 1.0
    return 0.5 ** (days / HALF_LIFE_DAYS)


# ====== 集計ロジック ========================================================

def _base_weight_of(rec: Dict[str, Any]) -> float:
    if "weight" in rec:
        try:
            return float(rec["weight"])
        except Exception:
            pass
    ch = str(rec.get("choice", "")).strip().lower()
    return CHOICE_WEIGHT.get(ch, 0.0)

def _effective_text(rec: Dict[str, Any]) -> Optional[str]:
    """edit/修正なら edited_text を優先、なければ text"""
    choice = str(rec.get("choice", "")).lower()
    if choice in ("edit","fix","修正","✏️") and rec.get("edited_text"):
        return str(rec["edited_text"]).strip()
    t = rec.get("text")
    return str(t).strip() if isinstance(t, str) else None

def _infer_tone_tags(text: str) -> List[str]:
    """テキストから簡易的にトーン/セクタータグを推測（軽いヒューリスティック）"""
    tags: List[str] = []
    s = text
    # 需給系
    if any(k in s for k in ["買いが優勢","強気","買い寄り","底堅"]):
        tags.append("tone:buy")
    if any(k in s for k in ["売りが優勢","慎重","警戒","売り寄り"]):
        tags.append("tone:sell")
    if "拮抗" in s or "様子見" in s or "静かな" in s:
        tags.append("tone:flat")
    # セクター系（代表的なものをピック）
    for kw, t in [
        ("半導体","sec:半導体"), ("生成AI","sec:AI"), ("素材","sec:素材"),
        ("エネルギー","sec:エネルギー"), ("金融","sec:金融"),
        ("ディフェンシブ","sec:ディフェンシブ"), ("インフラ","sec:インフラ"),
    ]:
        if kw in s:
            tags.append(t)
    return list(dict.fromkeys(tags))  # unique-preserving

def _load_existing_examples(path: str) -> Dict[str, Dict[str, Any]]:
    """examples.jsonl を読み込み、key->row の辞書で返す"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(path):
        text = row.get("text")
        mode = _norm_mode(row.get("mode"))
        if not isinstance(text, str) or not text.strip():
            continue
        key = row.get("key") or _mk_key(mode, text.strip())
        # 正規化
        r = {
            "key": key,
            "mode": mode,
            "text": text.strip(),
            "weight": float(row.get("weight", 0.0)),
            "ts_first": row.get("ts_first") or row.get("ts") or _now_iso(),
            "ts_last":  row.get("ts_last")  or row.get("ts") or _now_iso(),
            "count":    int(row.get("count", 1)),
            "tags":     _coerce_list(row.get("tags")),
            "notes":    row.get("notes") or "",
        }
        out[key] = r
    return out

def _aggregate_feedback(
    feedback_path: str,
    existing: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    feedback.jsonl を走査し、examples を更新して返す。
    併せて統計 (stats) を返す。
    """
    now = dt.datetime.now(dt.timezone.utc)
    stats: Dict[str, Any] = dict(
        total=0, up=0, down=0, edit=0,
        added=0, updated=0,
        by_mode=defaultdict(int),  # type: ignore
        weight_sum=0.0,
    )

    for rec in _read_jsonl(feedback_path):
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
            continue
        txt = " ".join(txt.split())  # normalize spaces
        key = _mk_key(mode, txt)

        tstamp = _parse_ts(rec.get("ts"))
        decay = _time_decay_factor(tstamp, now)
        eff_w = base_w * decay
        stats["weight_sum"] += eff_w

        tags = set(_coerce_list(rec.get("tags")))
        # 自動推測タグも付与
        for t in _infer_tone_tags(txt):
            tags.add(t)

        if key in existing:
            row = existing[key]
            # 既存重みを時間減衰（最終更新時刻に基づく）
            last_dt = _parse_ts(row.get("ts_last"))
            row_decay = _time_decay_factor(last_dt, now)
            row["weight"] = float(row.get("weight", 0.0)) * row_decay + eff_w
            row["ts_last"] = tstamp.isoformat()
            row["count"] = int(row.get("count", 1)) + 1
            # タグは和集合
            row["tags"] = list(sorted(set(row.get("tags", [])) | tags))
            stats["updated"] += 1
        else:
            # 新規追加
            existing[key] = dict(
                key=key, mode=mode, text=txt, weight=eff_w,
                ts_first=tstamp.isoformat(), ts_last=tstamp.isoformat(),
                count=1, tags=list(sorted(tags)), notes=rec.get("comment") or "",
            )
            stats["added"] += 1

    # クリーニング: NaN/Inf/極端値のクリップ
    for k, row in list(existing.items()):
        w = float(row.get("weight", 0.0))
        if math.isnan(w) or math.isinf(w):
            row["weight"] = 0.0
        else:
            # 軽いクリップ: [-2.5, +6.0]
            row["weight"] = max(-2.5, min(6.0, w))
    return existing, _freeze_stats(stats)

def _freeze_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    by_mode = {k: int(v) for k, v in getattr(stats["by_mode"], "items", lambda: [])()}
    out = dict(stats)
    out["by_mode"] = by_mode
    return out

def _prune_examples(examples: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    モードバランスと重みで並べ替え、件数制限を適用。
    ネガティブも少量は残す（バランス学習用）。ただし重いマイナスは落とす。
    """
    by_mode: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in examples.values():
        by_mode[row["mode"]].append(row)

    kept: List[Dict[str, Any]] = []
    for mode, rows in by_mode.items():
        # 重い順に並べるが、軽い負は末尾に少数残す
        rows.sort(key=lambda r: r["weight"], reverse=True)
        top_pos = [r for r in rows if r["weight"] > 0]
        negs    = [r for r in rows if r["weight"] <= 0]

        # 上位は多め、負は少なめ（情報量として少数保持）
        take_pos = top_pos[: int(N_PER_MODE * 0.9)]
        take_neg = negs[: max(5, int(N_PER_MODE * 0.1))]
        block = (take_pos + take_neg)[:N_PER_MODE]
        kept.extend(block)

    # 総量でクリップ
    kept.sort(key=lambda r: r["weight"], reverse=True)
    if len(kept) > N_MAX_TOTAL:
        kept = kept[:N_MAX_TOTAL]
    return kept


# ====== コマンド本体 ========================================================

class Command(BaseCommand):
    help = "LINEフィードバック(feedback.jsonl)を重み付きで学習し、few-shot用コーパス(gorozooo_examples.jsonl)を更新します。"

    def add_arguments(self, parser: CommandParser) -> None:
        p = _paths()
        parser.add_argument("--feedback", type=str, default=p["feedback"], help="フィードバックjsonlのパス")
        parser.add_argument("--examples", type=str, default=p["examples"], help="出力:例文jsonlのパス")
        parser.add_argument("--stats",    type=str, default=p["stats"],    help="出力:統計jsonのパス")
        parser.add_argument("--state",    type=str, default=p["state"],    help="学習状態の保存先")
        parser.add_argument("--half-life-days", type=float, default=HALF_LIFE_DAYS, help="重みの半減期(日)")
        parser.add_argument("--max-total", type=int, default=N_MAX_TOTAL, help="全体の最大件数")
        parser.add_argument("--per-mode",  type=int, default=N_PER_MODE,  help="モード別の最大件数")
        parser.add_argument("--dry-run",   action="store_true", help="出力を書き込まない（ログのみ）")

    def handle(self, *args, **opts):
        global HALF_LIFE_DAYS, N_MAX_TOTAL, N_PER_MODE
        HALF_LIFE_DAYS = float(opts["half_life_days"])
        N_MAX_TOTAL    = int(opts["max_total"])
        N_PER_MODE     = int(opts["per_mode"])

        feedback_path = opts["feedback"]
        examples_path = opts["examples"]
        stats_path    = opts["stats"]
        state_path    = opts["state"]
        dry_run       = bool(opts["dry_run"])

        self.stdout.write(self.style.HTTP_INFO(f"[learn] feedback={feedback_path}"))
        self.stdout.write(self.style.HTTP_INFO(f"[learn] examples={examples_path}"))
        self.stdout.write(self.style.HTTP_INFO(f"[learn] stats={stats_path}"))

        # 既存コーパス読込
        existing = _load_existing_examples(examples_path)
        n_before = len(existing)
        self.stdout.write(self.style.NOTICE(f"[load] examples: {n_before} rows"))

        # フィードバック集計
        updated, stats = _aggregate_feedback(feedback_path, existing)
        self.stdout.write(self.style.NOTICE(
            f"[aggregate] total_fb={stats['total']} up={stats['up']} edit={stats['edit']} down={stats['down']} added={stats['added']} updated={stats['updated']} weight_sum={stats['weight_sum']:.2f}"
        ))

        # プルーニング
        pruned = _prune_examples(updated)
        n_after = len(pruned)
        self.stdout.write(self.style.NOTICE(f"[prune] -> keep {n_after} rows (before {n_before})"))

        # 参考: トーンの重み合計
        tone_w = dict(buy=0.0, sell=0.0, flat=0.0)
        for r in pruned:
            w = float(r.get("weight", 0.0))
            tags = set(r.get("tags", []))
            if "tone:buy" in tags:  tone_w["buy"]  += w
            if "tone:sell" in tags: tone_w["sell"] += w
            if "tone:flat" in tags: tone_w["flat"] += w

        # 出力
        if not dry_run:
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