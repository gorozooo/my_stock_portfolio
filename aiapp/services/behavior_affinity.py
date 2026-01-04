# aiapp/services/behavior_affinity.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings


@dataclass
class AffinityResult:
    """
    セクター単位の「相性」情報。

    rank:
        "◎" / "○" / "△" / "×" / "？" / ""（データなし）
    win_rate:
        勝率 [%]。memory 側の値をそのまま使用（None のこともある）
    trials:
        試行回数（トレード数）
    avg_pl:
        1トレードあたり平均損益（円）。memory 側の値をそのまま使用。
    avg_r:
        1トレードあたり平均R。memory 側の値をそのまま使用。
    label:
        テンプレートでそのまま表示できる日本語ラベル
        例: "相性◎ 68%（15戦）"
    """
    rank: str
    win_rate: Optional[float]
    trials: int
    avg_pl: Optional[float]
    avg_r: Optional[float]
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "win_rate": self.win_rate,
            "trials": self.trials,
            "avg_pl": self.avg_pl,
            "avg_r": self.avg_r,
            "label": self.label,
        }


# ===== パラメータ（しきい値） =====

# 「ちゃんとサンプルがある」とみなす最低トレード数
MIN_TRIALS_FOR_CONFIDENT = 5

# 勝率によるランク判定しきい値
#   win_rate >= 65%      -> ◎
#   55% <= win_rate <65  -> ○
#   45% <= win_rate <55  -> △
#   win_rate < 45%       -> ×
RANK_BORDER_EXCELLENT = 65.0
RANK_BORDER_GOOD = 55.0
RANK_BORDER_NEUTRAL = 45.0


def _memory_base_dir() -> Path:
    """
    行動メモリ JSON のベースディレクトリ。

    aiapp/services/behavior_memory.save_behavior_memory() と同じ構成：
        MEDIA_ROOT / "aiapp" / "behavior" / "memory"
    """
    return Path(settings.MEDIA_ROOT) / "aiapp" / "behavior" / "memory"


def _load_memory(user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    latest_behavior_memory_u{user_id}.json を読み込む。

    - user_id が None の場合は "uall" を見にいく
    - 個別ファイルがなければ all をフォールバックで見る
    - どちらも無ければ None を返す
    """
    base_dir = _memory_base_dir()

    # user_id が無いときは "all" とみなす
    uid = user_id if user_id is not None else "all"

    # まずはユーザー別
    path_user = base_dir / f"latest_behavior_memory_u{uid}.json"

    # フォールバック: 全体（all）
    path_all = base_dir / "latest_behavior_memory_uall.json"

    target_path: Optional[Path] = None
    if path_user.exists():
        target_path = path_user
    elif path_all.exists():
        target_path = path_all

    if target_path is None or not target_path.exists():
        return None

    try:
        text = target_path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def _decide_rank(trials: int, win_rate: Optional[float]) -> str:
    """
    試行回数と勝率からランク記号を決定する。
    """
    if trials <= 0 or win_rate is None:
        # データなし
        return ""

    if trials < MIN_TRIALS_FOR_CONFIDENT:
        # サンプル不足
        return "？"

    # ここからは試行数充分とみなす
    if win_rate >= RANK_BORDER_EXCELLENT:
        return "◎"
    if win_rate >= RANK_BORDER_GOOD:
        return "○"
    if win_rate >= RANK_BORDER_NEUTRAL:
        return "△"
    return "×"


def _build_label(rank: str, trials: int, win_rate: Optional[float]) -> str:
    """
    日本語ラベルの組み立て。
    """
    if trials <= 0:
        return ""

    # 勝率がない場合
    if win_rate is None:
        if rank == "？":
            return f"データ不足（{trials}戦）"
        if rank:
            return f"相性{rank}（{trials}戦）"
        return f"{trials}戦（統計値不足）"

    # 勝率あり
    win_pct = f"{win_rate:.0f}"
    if rank:
        # 通常パターン
        return f"相性{rank} {win_pct}%（{trials}戦）"
    # ランク空（何らかの理由でランク付けしていない）
    return f"{win_pct}%（{trials}戦）"


def get_affinity_for_sector(
    user_id: Optional[int],
    sector_name: Optional[str],
) -> AffinityResult:
    """
    セクター名をキーに「相性情報」を取得するメイン関数。

    使い方（ビュー / サービス側）イメージ：
        from aiapp.services.behavior_affinity import get_affinity_for_sector

        affinity = get_affinity_for_sector(request.user.id, sector)
        affinity_dict = affinity.to_dict()
        # → テンプレに渡してバッジ表示などに使う

    sector_name:
        build_behavior_memory で保存された "sector" キーと同じ文字列を想定。
        None や空文字の場合は「データなし」として rank="" で返す。
    """
    # セクター名が無ければ即データなし扱い
    if not sector_name:
        return AffinityResult(
            rank="",
            win_rate=None,
            trials=0,
            avg_pl=None,
            avg_r=None,
            label="",
        )

    memory = _load_memory(user_id=user_id)
    if not memory:
        return AffinityResult(
            rank="",
            win_rate=None,
            trials=0,
            avg_pl=None,
            avg_r=None,
            label="",
        )

    sector_stats = memory.get("sector") or {}
    raw = sector_stats.get(sector_name)
    if not raw:
        # 完全一致が見つからない場合は、ちょっとだけ甘く見る（strip）
        normalized = {str(k).strip(): v for k, v in sector_stats.items()}
        raw = normalized.get(str(sector_name).strip())

    if not raw:
        return AffinityResult(
            rank="",
            win_rate=None,
            trials=0,
            avg_pl=None,
            avg_r=None,
            label="",
        )

    # StatBucket.to_dict() の構造に合わせて取り出し
    trials = int(raw.get("trials") or 0)
    wins = int(raw.get("wins") or 0)  # 今のところは使わないが一応読み込み
    win_rate = raw.get("win_rate")  # None の可能性あり
    avg_pl = raw.get("avg_pl")
    avg_r = raw.get("avg_r")

    # 型を軽く正規化
    try:
        win_rate_f: Optional[float]
        if win_rate is None:
            win_rate_f = None
        else:
            win_rate_f = float(win_rate)
    except Exception:
        win_rate_f = None

    try:
        avg_pl_f: Optional[float]
        if avg_pl is None:
            avg_pl_f = None
        else:
            avg_pl_f = float(avg_pl)
    except Exception:
        avg_pl_f = None

    try:
        avg_r_f: Optional[float]
        if avg_r is None:
            avg_r_f = None
        else:
            avg_r_f = float(avg_r)
    except Exception:
        avg_r_f = None

    rank = _decide_rank(trials=trials, win_rate=win_rate_f)
    label = _build_label(rank=rank, trials=trials, win_rate=win_rate_f)

    return AffinityResult(
        rank=rank,
        win_rate=win_rate_f,
        trials=trials,
        avg_pl=avg_pl_f,
        avg_r=avg_r_f,
        label=label,
    )