"""
[FILE] judge.py
[PATH] <project_root>/shihyo/services/judge.py

このファイルは何？
- 日経先物 / ドル円 / VIX を「初心者向けの言葉」に変換し、
  最後に「今日の行動」を決めるロジックです。
- 目的: ON/OFFみたいな抽象語をやめて、行動を直接表示する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Judged:
    nikkei_label: str
    fx_label: str
    vix_label: str
    action_title: str
    action_lines: List[str]


def label_nikkei(change_pct: float) -> str:
    # 先物の強弱（雑に見えるけど、初心者に必要なのはまずここ）
    if change_pct >= 1.2:
        return "強い（買い戻し優勢）"
    if change_pct <= -1.2:
        return "弱い（戻り売り警戒）"
    return "中立（方向待ち）"


def label_fx(change_pct: float) -> str:
    # USDJPYが上（円安）→日本株には追い風になりやすい
    if change_pct >= 0.3:
        return "追い風（円安）"
    if change_pct <= -0.3:
        return "向かい風（円高）"
    return "中立"


def label_vix(vix_last: float, vix_change_pct: float) -> str:
    # VIXの水準はよく使われる温度感の目安に寄せる
    if vix_last >= 25 or vix_change_pct >= 8:
        return "パニック寄り（荒れやすい）"
    if vix_last >= 15 or vix_change_pct >= 4:
        return "警戒（値動き荒め）"
    return "平常"


def decide_action(nikkei_change_pct: float, fx_change_pct: float, vix_last: float, vix_change_pct: float) -> Tuple[str, List[str]]:
    """
    “行動”をそのまま返す。
    ここがアプリの心臓。
    """
    # 危険判定（まず守る）
    if vix_last >= 25 or vix_change_pct >= 8 or nikkei_change_pct <= -1.0:
        return (
            "🔴 今は守る相場",
            [
                "新規で買わない（まず防御）",
                "持ってる銘柄はポジション軽くする検討",
                "底打ち確認（下げ止まり）を待つ",
            ],
        )

    # 攻めOK（ただし追いかけない）
    if nikkei_change_pct >= 0.8 and fx_change_pct >= 0.0 and vix_last < 20:
        return (
            "🟢 押し目で買える相場",
            [
                "急騰を追わない（高値で飛び乗らない）",
                "下がった所で少しずつ（分割）",
                "逆指値 or 損切りラインは先に決める",
            ],
        )

    # それ以外は様子見
    return (
        "🟡 今は様子見",
        [
            "いま買わない（方向が固まるまで待つ）",
            "強い銘柄だけ監視（置いていかれてもOK）",
            "前日の安値を割るなら一段注意",
        ],
    )


def judge(nikkei_change_pct: float, fx_change_pct: float, vix_last: float, vix_change_pct: float) -> Judged:
    n_label = label_nikkei(nikkei_change_pct)
    f_label = label_fx(fx_change_pct)
    v_label = label_vix(vix_last, vix_change_pct)
    action_title, action_lines = decide_action(nikkei_change_pct, fx_change_pct, vix_last, vix_change_pct)
    return Judged(
        nikkei_label=n_label,
        fx_label=f_label,
        vix_label=v_label,
        action_title=action_title,
        action_lines=action_lines,
    )