from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except Exception:
                continue
    except Exception:
        return rows
    return rows


def _calc_streak(wl_labels: List[str]) -> Tuple[str, int]:
    if not wl_labels:
        return ("none", 0)
    last = wl_labels[-1]
    n = 1
    for i in range(len(wl_labels) - 2, -1, -1):
        if wl_labels[i] == last:
            n += 1
        else:
            break
    return (last, n)


def _pick_focus(avg_r: Optional[float], win_rate: Optional[float], wl_total: int) -> str:
    if wl_total < 5:
        return "WLが少ない。まずは“同条件の再現”を増やして型を固定。"
    if avg_r is not None and avg_r < 0:
        return "平均Rがマイナス。負けの深さ（滑り/我慢/ロット）を最優先で矯正。"
    if win_rate is not None and win_rate < 45:
        return "命中が弱い。入り方より“入らないルール（NG条件）”を先に作る。"
    if win_rate is not None and win_rate >= 60 and (avg_r is None or avg_r < 0.2):
        return "命中は高い。次は“勝ちを伸ばす形（利確の早さ）”を改善。"
    return "安定帯。勝ちパターンを1つ固定して“同条件で連続再現”を狙う。"


def _make_lines(
    today_label: str,
    wl_total: int,
    wins: int,
    loses: int,
    win_rate: Optional[float],
    avg_r: Optional[float],
    avg_pl: Optional[float],
    streak_label: str,
    streak_len: int,
) -> List[str]:
    lines: List[str] = []

    # 1) 今日の状態
    if win_rate is None:
        lines.append(f"{today_label}｜PRO学習: WL={wl_total}（まだ勝率算出不可）")
    else:
        lines.append(f"{today_label}｜PRO学習: WL={wl_total} 勝率={win_rate:.1f}%（W{wins}/L{loses}）")

    # 2) 回収力（R）
    if avg_r is None:
        lines.append("平均R: -（R未記録の取引が多い）")
    else:
        lines.append(f"平均R: {avg_r:+.3f}（0より上=ルール上の回収が効いてる）")

    # 3) 現金感覚（PL）
    if avg_pl is None:
        lines.append("平均PL: -（PL未記録）")
    else:
        lines.append(f"平均PL: {avg_pl:+,.0f}円（carry/skipは0扱い）")

    # 4) 直近の偏り
    if streak_len >= 2 and streak_label in ("win", "lose"):
        tag = "WIN連" if streak_label == "win" else "LOSE連"
        lines.append(f"直近の偏り: {tag} ×{streak_len}（同条件の固定 or NG条件の抽出チャンス）")
    else:
        lines.append("直近の偏り: 目立った連続なし（次の“型”を作る余地）")

    # 5) 今日のフォーカス
    lines.append("今日のフォーカス: " + _pick_focus(avg_r, win_rate, wl_total))

    return lines[:5]


class Command(BaseCommand):
    help = "Generate daily behavior ticker text (PRO) and save to media/aiapp/behavior/ticker/latest_ticker_u{user}.json"

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, required=True, help="user id")
        parser.add_argument("--dry-run", action="store_true", help="do not write file")

    def handle(self, *args, **options):
        user_id = int(options["user"])
        dry = bool(options["dry_run"])

        User = get_user_model()
        try:
            User.objects.get(id=user_id)
        except Exception:
            raise CommandError(f"user not found: {user_id}")

        today = timezone.localdate()
        today_label = today.strftime("%Y-%m-%d")

        media_root = Path("media")
        beh_dir = media_root / "aiapp" / "behavior"
        model_path = beh_dir / "model" / f"latest_behavior_model_u{user_id}.json"
        side_path = beh_dir / "latest_behavior_side.jsonl"

        model = _read_json(model_path) or {}
        side_rows = _read_jsonl(side_path)

        # side から WL 並び
        wl_labels: List[str] = []
        wins = 0
        loses = 0
        for r in side_rows:
            lab = str(r.get("eval_label") or "").strip().lower()
            if lab in ("win", "lose"):
                wl_labels.append(lab)
                if lab == "win":
                    wins += 1
                else:
                    loses += 1

        wl_total = wins + loses

        # model から KPI
        win_rate = _safe_float(model.get("win_rate"))
        avg_r = _safe_float(model.get("avg_r"))
        avg_pl = _safe_float(model.get("avg_pl"))

        streak_label, streak_len = _calc_streak(wl_labels)

        lines = _make_lines(
            today_label=today_label,
            wl_total=wl_total,
            wins=wins,
            loses=loses,
            win_rate=win_rate,
            avg_r=avg_r,
            avg_pl=avg_pl,
            streak_label=streak_label,
            streak_len=streak_len,
        )

        out_dir = beh_dir / "ticker"
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "user_id": user_id,
            "date": today_label,
            "generated_at": timezone.now().isoformat(),
            "lines": lines,
            "meta": {
                "wl_total": wl_total,
                "wins": wins,
                "loses": loses,
                "streak_label": streak_label,
                "streak_len": streak_len,
            },
        }

        out_path = out_dir / f"latest_ticker_u{user_id}.json"

        if dry:
            self.stdout.write(self.style.WARNING("[generate_behavior_ticker] dry-run"))
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"[generate_behavior_ticker] wrote: {out_path}"))
        for line in lines:
            self.stdout.write(" - " + line)