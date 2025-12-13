# aiapp/services/confidence_service.py
# -*- coding: utf-8 -*-
"""
confidence_service.py

AI信頼度（⭐️1–5）の「唯一の司令塔」。

仕様（打ち合わせ版）をコードに固定する：
  1) 過去30〜90日の仮想エントリー成績（同モード）
  2) 特徴量の安定性（多様日で同様の傾向が出るか）
  3) 乖離（Entry→SL/TPまでの距離）が適正か

重要：
- picks_build 側は ⭐️の最終決定をここに丸投げする前提。
- BehaviorStats が “starsしか持っていない” 状態でも動く。
  もし BehaviorStats に n / win_rate / avg_r などの列が将来追加されたら、
  自動で重み付けに取り込む（getattr で読む）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

# scoring_service は「特徴量側の補助役」
try:
    from aiapp.services.scoring_service import score_sample, stars_from_score
except Exception:  # pragma: no cover
    score_sample = None  # type: ignore
    stars_from_score = None  # type: ignore

# BehaviorStats は「実績側（紙シミュ含む）」
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore


# =========================================================
# データクラス（デバッグ用途）
# =========================================================

@dataclass
class ConfidenceDetail:
    stars_final: int

    # 3本柱
    stars_perf: Optional[int]
    stars_stability: int
    stars_distance: int

    # 補助
    stars_score: int
    score01: Optional[float]

    # 参照先（実績）
    perf_source: str  # "mode" / "all" / "none"
    perf_n: Optional[int]
    perf_win_rate: Optional[float]
    perf_avg_r: Optional[float]

    # 合成重み
    w_perf: float
    w_stability: float
    w_distance: float
    w_score: float


# =========================================================
# 安全ユーティリティ
# =========================================================

def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        x = int(v)
    except Exception:
        return default
    return max(lo, min(hi, x))


def _nz_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if not np.isfinite(f):
            return default
        return float(f)
    except Exception:
        return default


def _safe_series(x) -> pd.Series:
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(dtype="float64")
        return x.iloc[:, -1].astype("float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _last_float(s: pd.Series) -> float:
    s = _safe_series(s).dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")


def _normalize_code(code: str) -> str:
    s = str(code or "").strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


# =========================================================
# 1) 実績（BehaviorStats）取得
# =========================================================

def _fetch_behavior_row(
    *,
    code: str,
    mode_period: str,
    mode_aggr: str,
) -> Tuple[Optional[object], str]:
    """
    BehaviorStats を取りに行く。
    優先順位：
      1) (code, mode_period, mode_aggr)
      2) (code, all, all)
      3) None
    戻り値: (row, source)  source = "mode" / "all" / "none"
    """
    if BehaviorStats is None:
        return None, "none"

    c = _normalize_code(code)
    mp = (mode_period or "").strip().lower() or "all"
    ma = (mode_aggr or "").strip().lower() or "all"

    # 1) 同モード
    try:
        row = (
            BehaviorStats.objects
            .filter(code=c, mode_period=mp, mode_aggr=ma)
            .values()
            .first()
        )
        if row:
            return row, "mode"
    except Exception:
        pass

    # 2) all/all
    try:
        row = (
            BehaviorStats.objects
            .filter(code=c, mode_period="all", mode_aggr="all")
            .values()
            .first()
        )
        if row:
            return row, "all"
    except Exception:
        pass

    return None, "none"


def _extract_perf_fields(row: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int], Optional[float], Optional[float]]:
    """
    BehaviorStats の row(dict) から取り出せるものを拾う。
    - stars: 必須（無いなら None）
    - n / count / trials など: あれば拾う
    - win_rate: あれば拾う（0..1 or 0..100 のどちらでも許容）
    - avg_r: あれば拾う
    """
    if not row:
        return None, None, None, None

    stars = row.get("stars")
    stars_i = None
    if stars is not None:
        try:
            stars_i = int(stars)
        except Exception:
            stars_i = None

    # n の候補
    n = None
    for k in ("n", "count", "trials", "num", "samples"):
        if k in row and row.get(k) is not None:
            try:
                n = int(row.get(k))
                break
            except Exception:
                n = None

    win_rate = None
    for k in ("win_rate", "winrate", "wr"):
        if k in row and row.get(k) is not None:
            try:
                wr = float(row.get(k))
                if np.isfinite(wr):
                    # 0..1 でも 0..100 でもOK
                    if wr > 1.0:
                        wr = wr / 100.0
                    win_rate = max(0.0, min(1.0, wr))
                    break
            except Exception:
                win_rate = None

    avg_r = None
    for k in ("avg_r", "mean_r", "avg_result_r", "mean_result_r"):
        if k in row and row.get(k) is not None:
            try:
                ar = float(row.get(k))
                if np.isfinite(ar):
                    avg_r = float(ar)
                    break
            except Exception:
                avg_r = None

    return stars_i, n, win_rate, avg_r


# =========================================================
# 2) 特徴量の安定性（多様日で同様の傾向）
# =========================================================

def _stability_score_0_1(feat: pd.DataFrame, window: int = 60) -> float:
    """
    「最後の状態（方向感）が、過去window日の中でどれだけ一貫しているか」を 0..1 で返す。
    目的：ノイズでコロコロ変わる銘柄を⭐️で落とす。

    使用する軸（ある分だけ使う）：
      - SLOPE_20（中期トレンド）
      - RET_20（中期モメンタム）
      - RSI14（>50 を上向き扱い）
      - SLOPE_5 / RET_5（短期も少し）
    """
    if feat is None or len(feat) < 10:
        return 0.50

    df = feat.copy()
    df = df.tail(max(10, int(window)))

    def col(name: str) -> Optional[pd.Series]:
        if name not in df.columns:
            return None
        s = _safe_series(df[name])
        if len(s) == 0:
            return None
        return s

    axes = []

    s20 = col("SLOPE_20")
    r20 = col("RET_20")
    rsi = col("RSI14")
    s5 = col("SLOPE_5")
    r5 = col("RET_5")

    if s20 is not None:
        axes.append(("sign", s20))
    if r20 is not None:
        axes.append(("sign", r20))
    if rsi is not None:
        axes.append(("rsi", rsi))
    if s5 is not None:
        axes.append(("sign", s5))
    if r5 is not None:
        axes.append(("sign", r5))

    if not axes:
        return 0.50

    scores = []
    for kind, s in axes:
        s = _safe_series(s)
        s = pd.to_numeric(s, errors="coerce").dropna()
        if len(s) < 10:
            continue

        tail = s.iloc[-1]
        if not np.isfinite(float(tail)):
            continue

        if kind == "rsi":
            # RSIは 50 を境に上/下
            last_state = 1 if float(tail) >= 50.0 else -1
            states = np.where(s.values >= 50.0, 1, -1)
        else:
            # sign
            last_state = 1 if float(tail) >= 0.0 else -1
            states = np.where(s.values >= 0.0, 1, -1)

        same = float(np.mean(states == last_state))
        scores.append(same)

    if not scores:
        return 0.50

    # 軸の平均
    v = float(np.mean(scores))
    return max(0.0, min(1.0, v))


def _stars_from_stability(stab01: float) -> int:
    """
    安定性 → ⭐️
    """
    s = _nz_float(stab01, 0.5)
    if s >= 0.80:
        return 5
    if s >= 0.70:
        return 4
    if s >= 0.60:
        return 3
    if s >= 0.50:
        return 2
    return 1


# =========================================================
# 3) 距離適正（Entry→SL/TPの妥当性）
# =========================================================

def _distance_score_0_1(
    *,
    feat: pd.DataFrame,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
) -> float:
    """
    Entry/TP/SL の距離が ATR 比で見て極端じゃないかを 0..1 で返す。
    目的：
      - 近すぎるSL（刈られやすい）
      - 遠すぎるSL（損失大）
      - TPが薄い（RRが悪い）
      を⭐️で落とす
    """
    if entry is None or tp is None or sl is None:
        return 0.55
    try:
        e = float(entry); t = float(tp); s = float(sl)
        if not (np.isfinite(e) and np.isfinite(t) and np.isfinite(s)):
            return 0.55
    except Exception:
        return 0.55

    # ATR を特徴量から拾う（ATR14 があればそれを優先）
    atr = float("nan")
    if feat is not None and len(feat) > 0:
        if "ATR14" in feat.columns:
            atr = _last_float(feat.get("ATR14"))
        else:
            # 予備：ATRxx があれば最後のを拾う（例: ATR20）
            atr_cols = [c for c in feat.columns if isinstance(c, str) and c.upper().startswith("ATR")]
            if atr_cols:
                atr = _last_float(feat.get(atr_cols[0]))

    if not np.isfinite(atr) or atr <= 0:
        return 0.55

    risk = (e - s) / atr
    reward = (t - e) / atr

    if not (np.isfinite(risk) and np.isfinite(reward)):
        return 0.55

    # 変な向き（SLが上、TPが下など）
    if risk <= 0 or reward <= 0:
        return 0.10

    rr = reward / risk if risk > 0 else 0.0

    # --- スコアリング（ラフだけど本番で安定するやつ） ---
    score = 1.0

    # risk（ATR比）の適正帯：0.5〜1.3 を中心に
    if risk < 0.35:
        score *= 0.55
    elif risk < 0.50:
        score *= 0.75
    elif risk <= 1.30:
        score *= 1.00
    elif risk <= 1.80:
        score *= 0.80
    else:
        score *= 0.60

    # reward（ATR比）：0.6〜2.5 を中心に
    if reward < 0.50:
        score *= 0.60
    elif reward < 0.80:
        score *= 0.80
    elif reward <= 2.50:
        score *= 1.00
    elif reward <= 3.50:
        score *= 0.90
    else:
        score *= 0.75

    # RR：最低 1.0、理想 1.2〜2.0
    if rr < 0.9:
        score *= 0.60
    elif rr < 1.1:
        score *= 0.80
    elif rr <= 2.2:
        score *= 1.00
    else:
        score *= 0.90

    score = max(0.0, min(1.0, float(score)))
    return score


def _stars_from_distance(dist01: float) -> int:
    s = _nz_float(dist01, 0.55)
    if s >= 0.92:
        return 5
    if s >= 0.82:
        return 4
    if s >= 0.70:
        return 3
    if s >= 0.58:
        return 2
    return 1


# =========================================================
# 合成（重み付きハイブリッド）
# =========================================================

def _weights_from_perf(n: Optional[int], has_perf: bool) -> Tuple[float, float, float, float]:
    """
    重みの設計思想：
      - 実績（perf）は “件数が増えるほど強く信じる”
      - 安定性（stability）と距離（distance）は常に入れる
      - scoring_service は補助輪（最後の埋め合わせ）

    返り値: (w_perf, w_stability, w_distance, w_score)
    """
    w_stab = 0.22
    w_dist = 0.18

    if not has_perf:
        w_perf = 0.0
        # 実績が無い分は scoring に渡す
        w_score = 1.0 - (w_stab + w_dist)
        w_score = max(0.0, min(1.0, w_score))
        return 0.0, w_stab, w_dist, w_score

    # perf がある場合：nで強さを変える
    # n=0..5: 弱い / 10: 中 / 20+: 強い
    nn = _nz_float(n, 0.0)
    # 0..1 に正規化（20で上限）
    alpha = max(0.0, min(1.0, nn / 20.0))
    # perf最大0.65、最小0.35（存在するだけで一定重みを持つ）
    w_perf = 0.35 + 0.30 * alpha

    # 残りを scoring に（ただし補助輪なので上限を小さく）
    w_score = 1.0 - (w_perf + w_stab + w_dist)
    w_score = max(0.05, w_score)  # 最低ちょい残す（0にしない）
    # 正規化（合計1.0に）
    total = w_perf + w_stab + w_dist + w_score
    if total <= 0:
        return 0.0, 0.22, 0.18, 0.60
    return (w_perf / total, w_stab / total, w_dist / total, w_score / total)


def _weighted_round_star(
    *,
    stars_perf: Optional[int],
    stars_stab: int,
    stars_dist: int,
    stars_score: int,
    w_perf: float,
    w_stability: float,
    w_distance: float,
    w_score: float,
) -> int:
    def f(x: Optional[int]) -> float:
        if x is None:
            return float("nan")
        try:
            v = float(x)
            if not np.isfinite(v):
                return float("nan")
            return v
        except Exception:
            return float("nan")

    p = f(stars_perf)
    s = float(stars_stab)
    d = float(stars_dist)
    c = float(stars_score)

    # perf が None のときは w_perf を0扱いに寄せる（保険）
    if not np.isfinite(p):
        w_perf = 0.0

    val = 0.0
    wsum = 0.0
    if w_perf > 0 and np.isfinite(p):
        val += w_perf * p
        wsum += w_perf
    if w_stability > 0:
        val += w_stability * s
        wsum += w_stability
    if w_distance > 0:
        val += w_distance * d
        wsum += w_distance
    if w_score > 0:
        val += w_score * c
        wsum += w_score

    if wsum <= 0:
        return 1

    out = int(round(val / wsum))
    return max(1, min(5, out))


# =========================================================
# 公開API（picks_build から呼ぶ）
# =========================================================

def compute_confidence_detail(
    *,
    code: str,
    feat_df: pd.DataFrame,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,
) -> ConfidenceDetail:
    """
    最終⭐️と、内訳（3本柱＋補助）を返すデバッグ用API。
    """
    # --- 実績（BehaviorStats） ---
    row, src = _fetch_behavior_row(code=code, mode_period=mode_period, mode_aggr=mode_aggr)
    stars_perf, n, win_rate, avg_r = _extract_perf_fields(row if isinstance(row, dict) else None)

    has_perf = isinstance(stars_perf, int) and 1 <= stars_perf <= 5

    # --- 特徴量スコア（補助輪） ---
    score01 = None
    stars_score = 1
    if score_sample is not None:
        try:
            score01 = float(score_sample(feat_df, regime=regime))
        except TypeError:
            score01 = float(score_sample(feat_df))  # 古いシグネチャ保険
        except Exception:
            score01 = None

    if stars_from_score is not None and score01 is not None:
        try:
            stars_score = int(stars_from_score(score01))
        except Exception:
            stars_score = 1
    else:
        # 最低限の安全フォールバック
        if score01 is None or (not np.isfinite(float(score01))):
            stars_score = 1
        else:
            s = float(score01)
            if s < 0.20:
                stars_score = 1
            elif s < 0.40:
                stars_score = 2
            elif s < 0.60:
                stars_score = 3
            elif s < 0.80:
                stars_score = 4
            else:
                stars_score = 5

    # --- 安定性 ---
    stab01 = _stability_score_0_1(feat_df, window=60)
    stars_stab = _stars_from_stability(stab01)

    # --- 距離適正 ---
    dist01 = _distance_score_0_1(feat=feat_df, entry=entry, tp=tp, sl=sl)
    stars_dist = _stars_from_distance(dist01)

    # --- 重み ---
    w_perf, w_stab, w_dist, w_score = _weights_from_perf(n=n, has_perf=has_perf)

    # --- 合成 ---
    stars_final = _weighted_round_star(
        stars_perf=stars_perf if has_perf else None,
        stars_stab=stars_stab,
        stars_dist=stars_dist,
        stars_score=stars_score,
        w_perf=w_perf,
        w_stability=w_stab,
        w_distance=w_dist,
        w_score=w_score,
    )

    return ConfidenceDetail(
        stars_final=int(stars_final),
        stars_perf=int(stars_perf) if has_perf else None,
        stars_stability=int(stars_stab),
        stars_distance=int(stars_dist),
        stars_score=int(stars_score),
        score01=float(score01) if score01 is not None and np.isfinite(float(score01)) else None,
        perf_source=src,
        perf_n=int(n) if n is not None else None,
        perf_win_rate=float(win_rate) if win_rate is not None else None,
        perf_avg_r=float(avg_r) if avg_r is not None else None,
        w_perf=float(w_perf),
        w_stability=float(w_stab),
        w_distance=float(w_dist),
        w_score=float(w_score),
    )


def compute_confidence_star(
    *,
    code: str,
    feat_df: pd.DataFrame,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,
) -> int:
    """
    本番用：最終⭐️(1..5)だけ返す。
    """
    d = compute_confidence_detail(
        code=code,
        feat_df=feat_df,
        entry=entry,
        tp=tp,
        sl=sl,
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        regime=regime,
    )
    return int(_clamp_int(d.stars_final, 1, 5, 1))