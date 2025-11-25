# aiapp/views/behavior.py
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


Number = Optional[float]


# =========================================================
# 内部ユーティリティ
# =========================================================

def _parse_float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    ISO文字列 ts を timezone-aware datetime に。
    失敗したら None。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _dedupe_records(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同一銘柄・同一モード・同一エントリー価格の重複を 1 件にまとめる。
    キー：
        (code, price_date, mode, round(entry, 3))
    後から来たレコードで上書き（＝最新の評価を採用）。
    """
    dedup: Dict[Tuple[str, str, str, float], Dict[str, Any]] = {}

    # ts が古い順にしてから上書きすると、最終的に「最新」が残る
    ordered = sorted(
        raw_records,
        key=lambda r: (_parse_ts(r.get("ts")) or timezone.datetime.min.replace(tzinfo=timezone.utc))
    )

    for rec in ordered:
        code = str(rec.get("code") or "").strip()
        mode = str(rec.get("mode") or "").lower()
        price_date = str(rec.get("price_date") or "")
        entry = _parse_float(rec.get("entry")) or 0.0
        key = (code, price_date, mode, round(entry, 3))
        dedup[key] = rec

    return list(dedup.values())


def _time_bucket(dt: Optional[timezone.datetime]) -> str:
    """
    時刻をざっくりバケツ分け。
    """
    if dt is None:
        return "不明"
    h = dt.hour
    if 6 <= h < 9:
        return "朝 6-9時"
    if 9 <= h < 12:
        return "午前 9-12時"
    if 12 <= h < 15:
        return "午後 12-15時"
    if 15 <= h < 18:
        return "夕方 15-18時"
    return "夜間"


@dataclass
class SectorStat:
    name: str
    trials: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float:
        if self.trials <= 0:
            return 0.0
        return 100.0 * self.wins / self.trials


# =========================================================
# メインビュー
# =========================================================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    AI 自動シミュレ × 市場結果 × 特徴量 を統合した
    「行動学習ダッシュボード」画面。
    """
    user = request.user
    media_root = Path(settings.MEDIA_ROOT)
    behavior_dir = media_root / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"

    records: List[Dict[str, Any]] = []

    if latest_path.exists():
        try:
            text = latest_path.read_text(encoding="utf-8")
        except Exception:
            text = ""

        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue

            # ログインユーザーの分だけ
            if rec.get("user_id") != user.id:
                continue

            dt = _parse_ts(rec.get("ts"))
            rec["_dt"] = dt
            rec["ts_label"] = dt.strftime("%Y-%m-%d %H:%M") if dt else str(rec.get("ts") or "")
            records.append(rec)

    # データが無ければ空画面
    if not records:
        ctx = {
            "has_data": False,
            "total": 0,
        }
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    # 重複排除
    records = _dedupe_records(records)

    # ts 降順にソート（新しい順）
    records.sort(
        key=lambda r: (r.get("_dt") or timezone.datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    total = len(records)

    # -----------------------------------------------------
    # モード内訳
    # -----------------------------------------------------
    mode_counts = Counter(str(r.get("mode") or "").lower() for r in records)
    mode_counts = {
        "live": mode_counts.get("live", 0),
        "demo": mode_counts.get("demo", 0),
        "other": total - mode_counts.get("live", 0) - mode_counts.get("demo", 0),
    }

    # -----------------------------------------------------
    # 勝敗サマリ（楽天 / 松井）
    # -----------------------------------------------------
    def _pl_summary(broker: str) -> Dict[str, int]:
        labels = Counter()
        for r in records:
            label = r.get(f"eval_label_{broker}")
            if label in ("win", "lose", "flat", "no_position"):
                labels[label] += 1
            else:
                labels["none"] += 1
        return {
            "win": labels.get("win", 0),
            "lose": labels.get("lose", 0),
            "flat": labels.get("flat", 0),
            "no_position": labels.get("no_position", 0),
            "none": labels.get("none", 0),
        }

    pl_counts_r = _pl_summary("rakuten")
    pl_counts_m = _pl_summary("matsui")

    # -----------------------------------------------------
    # セクター別勝率（楽天）
    # -----------------------------------------------------
    sector_map: Dict[str, SectorStat] = {}

    for r in records:
        sec = str(r.get("sector") or "(未分類)")
        label = r.get("eval_label_rakuten")
        if sec not in sector_map:
            sector_map[sec] = SectorStat(name=sec)
        st = sector_map[sec]

        if label in ("win", "lose", "flat"):
            st.trials += 1
            if label == "win":
                st.wins += 1

    sector_stats: List[SectorStat] = sorted(
        sector_map.values(),
        key=lambda s: (-s.trials, s.name),
    )

    # -----------------------------------------------------
    # トレンド / 時間帯 / ATR / slope の簡易集計
    # -----------------------------------------------------
    trend_counter = Counter()
    time_counter = Counter()
    atr_counter = Counter()
    slope_counter = Counter()

    r_values: List[float] = []  # 全トレードの R（楽天）

    for r in records:
        # trend_daily
        trend = str(r.get("trend_daily") or "").lower()
        if trend in ("up", "down", "flat"):
            if trend == "up":
                trend_counter["上昇トレンド"] += 1
            elif trend == "down":
                trend_counter["下降トレンド"] += 1
            else:
                trend_counter["レンジ"] += 1
        else:
            trend_counter["不明"] += 1

        # 時間帯
        dt = r.get("_dt")
        time_bucket = _time_bucket(dt)
        time_counter[time_bucket] += 1

        # ATR 帯（ATR / entry の比率）
        entry = _parse_float(r.get("entry"))
        atr = _parse_float(r.get("atr_14") or r.get("atr"))
        if entry and atr:
            atr_pct = 100.0 * atr / entry
            if atr_pct < 1.0:
                atr_counter["〜1%"] += 1
            elif atr_pct < 2.0:
                atr_counter["1〜2%"] += 1
            elif atr_pct < 3.0:
                atr_counter["2〜3%"] += 1
            else:
                atr_counter["3%以上"] += 1
        else:
            atr_counter["不明"] += 1

        # slope 帯
        slope = _parse_float(r.get("slope_20"))
        if slope is None:
            slope_counter["不明"] += 1
        else:
            if slope < 0:
                slope_counter["マイナス傾き"] += 1
            elif slope < 3:
                slope_counter["なだらか"] += 1
            elif slope < 7:
                slope_counter["適度に強い"] += 1
            else:
                slope_counter["かなり強い"] += 1

        # R 値
        r_val = _parse_float(r.get("eval_r_rakuten"))
        if r_val is not None:
            r_values.append(r_val)

    def _counter_to_list(c: Counter) -> List[Dict[str, Any]]:
        total_c = sum(c.values())
        result: List[Dict[str, Any]] = []
        for name, cnt in c.most_common():
            pct = (100.0 * cnt / total_c) if total_c > 0 else 0.0
            result.append({"name": name, "count": cnt, "pct": pct})
        return result

    trend_stats = _counter_to_list(trend_counter)
    time_stats = _counter_to_list(time_counter)
    atr_stats = _counter_to_list(atr_counter)
    slope_stats = _counter_to_list(slope_counter)

    # -----------------------------------------------------
    # TOP 勝ち / 負け（楽天PLベース）
    # -----------------------------------------------------
    def _top_pl(label: str, limit: int = 5) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for r in records:
            if r.get("eval_label_rakuten") != label:
                continue
            pl = _parse_float(r.get("eval_pl_rakuten"))
            if pl is None:
                continue
            items.append(
                {
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "mode": r.get("mode"),
                    "pl": pl,
                    "ts": r.get("ts"),
                    "ts_label": r.get("ts_label"),
                }
            )
        # 勝ちは PL 降順、負けは昇順
        reverse = label == "win"
        items.sort(key=lambda x: x["pl"], reverse=reverse)
        return items[:limit]

    top_win = _top_pl("win", limit=5)
    top_lose = _top_pl("lose", limit=5)

    # -----------------------------------------------------
    # AI インサイト（自然言語メモ）
    # -----------------------------------------------------
    insights: List[str] = []

    # 勝率ベースの短いまとめ
    trials_r = pl_counts_r["win"] + pl_counts_r["lose"] + pl_counts_r["flat"]
    if trials_r > 0:
        win_rate_total = 100.0 * pl_counts_r["win"] / trials_r
        insights.append(
            f"紙トレ全体の勝率はおよそ {win_rate_total:.1f}% です（対象 {trials_r} トレード）。"
        )

    # セクターの得意・不得意
    sectors_with_trials = [s for s in sector_stats if s.trials >= 2]
    if sectors_with_trials:
        best = max(sectors_with_trials, key=lambda s: s.win_rate)
        worst = min(sectors_with_trials, key=lambda s: s.win_rate)
        insights.append(
            f"現時点では「{best.name}」が最も好成績で、勝率 {best.win_rate:.1f}%（{best.wins}/{best.trials}）です。"
        )
        if worst.name != best.name:
            insights.append(
                f"一方で「{worst.name}」は勝率 {worst.win_rate:.1f}% と伸び悩んでいます。ロットを抑えるか、条件を絞ると良さそうです。"
            )

    # 時間帯の得意・不得意
    if time_stats:
        best_time = max(time_stats, key=lambda x: x["pct"])
        insights.append(
            f"エントリー時刻は「{best_time['name']}」の比率が最も高く、全体の {best_time['pct']:.1f}% を占めています。"
        )

    # R 値の傾向
    if r_values:
        avg_r = sum(r_values) / len(r_values)
        if avg_r > 0:
            insights.append(
                f"R ベースの平均成績は {avg_r:.2f}R とプラス傾向です。利確幅に対して損切り幅が適切にコントロールできています。"
            )
        else:
            insights.append(
                f"R ベースの平均成績は {avg_r:.2f}R とマイナス寄りです。損切りまでの距離を広げすぎていないか、または利確が早すぎないかを見直す余地があります。"
            )

    # データ件数が少ないときの注意
    if total < 20:
        insights.append(
            "まだサンプル数が少ないため、傾向は暫定です。紙トレを重ねるほど、あなた専用のAIアドバイザーが賢くなります。"
        )

    ctx = {
        "has_data": True,
        "total": total,
        "mode_counts": mode_counts,
        "pl_counts_r": pl_counts_r,
        "pl_counts_m": pl_counts_m,
        "sector_stats": sector_stats,
        "trend_stats": trend_stats,
        "time_stats": time_stats,
        "atr_stats": atr_stats,
        "slope_stats": slope_stats,
        "top_win": top_win,
        "top_lose": top_lose,
        "insights": insights,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)