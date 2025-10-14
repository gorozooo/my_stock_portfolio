# -*- coding: utf-8 -*-
"""
AIアドバイザー学習コマンド（依存なし・シンプル自己学習）
- 過去の AdviceSession と AdviceItem を読み込み
- “その時のKPI” と “数日後のKPI” を比較して改善度(Improvement)を算出
- 各提案(メッセージ/カテゴリ)ごとの効果を集計してポリシーJSONとして保存
- さらに、未評価の AdviceItem.outcome を自動で埋める

実行:
    python manage.py advisor_learn
オプション例:
    python manage.py advisor_learn --horizon 7 --out media/advisor/policy.json --dry-run

このコマンドは追加のライブラリに依存しません（scikit-learn不要）。
将来、本格MLへ拡張したい場合は、ここで保存する policy.json を
“事前確率/重み”として使い、学習済みモデルの出力と合成してください。
"""
from __future__ import annotations
import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ...models_advisor import AdviceSession, AdviceItem


# ------------------------------
# 設定 / 型
# ------------------------------
DEFAULT_HORIZON_DAYS = 7          # 何日後のKPIで“改善度”を測るか
DEFAULT_OUTPUT_PATH  = "media/advisor/policy.json"
MIN_ITEMS_PER_GROUP  = 3          # 推定に使う最小件数（少なすぎると信頼度↓）

# メッセージ文面から“カテゴリ”へマッピング（将来Kindに移行可能）
CATEGORY_PATTERNS = [
    ("GAP",     ["乖離", "評価ROIと現金ROI"]),      # ROIギャップ整理
    ("LIQ",     ["流動性", "現金化余地"]),          # 流動性アップ
    ("MARGIN",  ["信用比率", "レバレッジ"]),        # 信用圧縮
    ("SECTOR",  ["セクター偏在"]),                  # セクター分散
    ("UNCAT",   ["未分類セクター", "業種タグ"]),     # 未分類タグ整備
    ("REALIZE", ["実現益", "利確", "段階的利確"]),   # 含み益の部分利確
    ("NEGROI",  ["評価ROIが", "損失限定"]),          # 守り提案
]


@dataclass
class Outcome:
    """
    1つの提案の“改善度”を標準化して持つための構造体。
    score:   -1.0 ~ +1.0 で正規化した改善度（0より大きい＝良い）
    details: 計算根拠（デルタ内訳）
    """
    score: float
    details: Dict


# ------------------------------
# ユーティリティ
# ------------------------------
def _get_category(message: str) -> str:
    msg = message or ""
    for cat, keys in CATEGORY_PATTERNS:
        if any(k in msg for k in keys):
            return cat
    return "OTHER"


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _improve_between(kpi0: Dict, kpi1: Dict) -> Outcome:
    """
    KPIの“良くなった/悪くなった”を1つのスカラーにまとめる簡易ルール。
    足し合わせる重みは軽め（将来ここを学習可）
    - ROI_eval_pct：上がると◎
    - liquidity_rate_pct：上がると◎
    - margin_ratio_pct：下がると◎
    それぞれ -1.0〜+1.0 にクリップして平均。
    """
    if not kpi0 or not kpi1:
        return Outcome(0.0, {"reason": "missing_kpi"})

    d_roi = _safe_float(kpi1.get("roi_eval_pct")) - _safe_float(kpi0.get("roi_eval_pct"))
    d_liq = _safe_float(kpi1.get("liquidity_rate_pct")) - _safe_float(kpi0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(kpi0.get("margin_ratio_pct")) - _safe_float(kpi1.get("margin_ratio_pct"))  # 低いほど◎ → 逆符号

    # ざっくり正規化（±50ptを±1.0扱い / リクイディティ±40ptを±1.0 / マージン±40ptを±1.0）
    def clip(x, s):  # scale
        if s <= 0:
            return 0.0
        v = max(-1.0, min(1.0, x / s))
        return v

    roi_norm = clip(d_roi, 50.0)
    liq_norm = clip(d_liq, 40.0)
    mrg_norm = clip(d_mrg, 40.0)

    score = (roi_norm + liq_norm + mrg_norm) / 3.0
    return Outcome(
        score=score,
        details={
            "d_roi": d_roi,
            "d_liq": d_liq,
            "d_margin_ratio": -d_mrg,  # 見やすさのために“増減”も残す
            "roi_norm": roi_norm,
            "liq_norm": liq_norm,
            "mrg_norm": mrg_norm,
        },
    )


def _find_future_session(all_sessions: List[AdviceSession], base: AdviceSession, horizon_days: int) -> Optional[AdviceSession]:
    """base.created_at から horizon_days 以降で、一番近いセッションを返す"""
    target = base.created_at + timedelta(days=horizon_days)
    later = [s for s in all_sessions if s.created_at >= target]
    return later[-1] if later else (later[0] if later else None)


# ------------------------------
# 学習本体
# ------------------------------
def learn_policy(horizon_days: int, dry_run: bool = False) -> Dict:
    """
    1) 全セッションを時系列に並べる
    2) 各セッションについて horizon_days 日後のセッションを探す
    3) KPI差分から Outcome を推定し、AdviceItem.outcome が空なら保存
    4) カテゴリ/メッセージごとに“成功率/平均改善度”を集計
    5) policy.json に保存するための辞書を返す
    """
    sessions: List[AdviceSession] = list(AdviceSession.objects.order_by("created_at"))
    if not sessions:
        return {"summary": {"sessions": 0}}

    # 集計用
    cat_stats: Dict[str, Dict[str, float]] = {}
    msg_stats: Dict[str, Dict[str, float]] = {}

    def _inc(d: Dict[str, Dict[str, float]], key: str, score: float):
        s = d.setdefault(key, {"n": 0, "sum": 0.0, "wins": 0})
        s["n"] += 1
        s["sum"] += score
        if score > 0:
            s["wins"] += 1

    with transaction.atomic():
        for i, s in enumerate(sessions):
            future = _find_future_session(sessions, s, horizon_days)
            if not future:
                continue
            k0 = s.context_json or {}
            k1 = future.context_json or {}
            outcome = _improve_between(k0, k1)

            # セッション内のアイテムに結果を反映（未設定のみ）
            for it in s.items.all().order_by("-score", "-id"):
                cat = _get_category(it.message or "")
                msg = (it.message or "").strip()
                # outcome未設定なら埋める（軽量）
                if not it.outcome:
                    it.outcome = {"score": outcome.score, "details": outcome.details, "horizon_days": horizon_days}
                    it.save(update_fields=["outcome"])

                # 学習用：採用した提案を優先してカウント
                if it.taken:
                    _inc(cat_stats, cat, outcome.score)
                    _inc(msg_stats, msg, outcome.score)

        if dry_run:
            # まとめてロールバックしたい場合 → トランザクション外で True を見て何もしない
            pass

    # 集計をポリシー形式に変換
    def _to_view(d: Dict[str, Dict[str, float]]) -> Dict[str, Dict]:
        out = {}
        for k, s in d.items():
            n = int(s["n"])
            if n == 0:
                continue
            avg = s["sum"] / max(1, n)
            win = s["wins"] / max(1, n)
            # 信頼度: 件数で重み付け（単純にlogスケール）
            conf = min(1.0, math.log10(1 + n) / math.log10(1 + max(n, 10)))
            out[k] = {"count": n, "avg_improve": round(avg, 4), "win_rate": round(win, 4), "confidence": round(conf, 4)}
        return out

    policy = {
        "generated_at": timezone.now().isoformat(),
        "horizon_days": horizon_days,
        "summary": {"sessions": len(sessions)},
        "category": _to_view(cat_stats),
        "message": _to_view(msg_stats),
        # 将来: ここに学習済みモデルの係数/パラメータを格納してもOK
    }
    return policy


def save_policy_json(policy: Dict, out_path: str) -> str:
    """policy を JSON として保存し、保存先パスを返す"""
    if not out_path:
        out_path = DEFAULT_OUTPUT_PATH

    # 絶対パス解決（MEDIA_ROOT 起点が分かりやすい）
    if not os.path.isabs(out_path):
        base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        out_path = os.path.join(base, out_path)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, indent=2)
    return out_path


# ------------------------------
# Django management command
# ------------------------------
class Command(BaseCommand):
    help = "AIアドバイザー: 提案結果から簡易ポリシーを学習し、policy.json を生成します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
                            help=f"何日後のKPIで改善を評価するか（既定: {DEFAULT_HORIZON_DAYS}）")
        parser.add_argument("--out", type=str, default=DEFAULT_OUTPUT_PATH,
                            help=f"保存先パス（既定: {DEFAULT_OUTPUT_PATH}。MEDIA_ROOT 相対可）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新を伴う保存を抑制（集計のみ）")
        parser.add_argument("--print", action="store_true", help="policyのサマリを標準出力に表示")

    def handle(self, *args, **opts):
        horizon = int(opts["horizon"])
        out_path = str(opts["out"])
        dry = bool(opts["dry_run"])
        do_print = bool(opts["print"])

        policy = learn_policy(horizon_days=horizon, dry_run=dry)
        saved = save_policy_json(policy, out_path)

        if do_print:
            self.stdout.write(json.dumps(policy, ensure_ascii=False, indent=2))

        self.stdout.write(self.style.SUCCESS(f"[advisor_learn] policy saved -> {saved}"))