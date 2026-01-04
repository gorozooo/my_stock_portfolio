# aiapp/models/scoring.py
# ─────────────────────────────────────────────────────────────────────────────
# 目的：
#  - 「総合得点＝妙味（期待値）」と「AI信頼度＝再現性（確からしさ）」を分離
#  - 日々の“宇宙（ユニバース）”内パーセンタイルで相対校正
#  - 乖離/過熱/流動性/ボラ等で現実的な減点
#  - ⭐️5連発/100点連発を構造的に防止（非線形圧縮＋ゲーティング）
#
# 既存の compute_features(df) を前提：
#  df: 日次のDataFrame（index=Timestamp, 昨日→今日へ昇順）
#  必要カラム（欠損OK：内部でフォールバック）
#   - close              : 終値
#   - rsi14              : RSI(14)
#   - macd_hist          : MACDヒストグラム
#   - vwap_dev_pct       : 終値とVWAPの乖離（%）
#   - ret_5d_pct         : 直近5日リターン（%）
#   - slope_ema_fast     : 短期EMAの傾き（近似でもOK, 正/負）
#   - atr                : ATR(14)（円）
#   - atr_pct            : ATR / close * 100（%）
#   - vol20              : 20日平均出来高（株）
#   - vwap               : VWAP（任意, 乖離計算済みなら不要）
#
# 提供関数：
#   - score_sample(feat, mode='aggressive', horizon='short') -> float（“生”スコア）
#   - score_batch(feat_map, mode='aggressive', horizon='short')
#        -> {code: {'raw':..., 'points':..., 'stars':..., 'extras':{...}}}
#     ※ picks側はこれを使うと、校正まで一括で取得できます。
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
import math

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# 小物ユーティリティ
# ─────────────────────────────────────────────────────────────────────────────
def _nz(x, default=0.0):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return default
    except Exception:
        pass
    return float(x)

def _clip(x, lo, hi):
    return float(max(lo, min(hi, x)))

def _last(feat: pd.DataFrame, col: str, default=np.nan):
    if col in feat.columns and len(feat[col].dropna()) > 0:
        return float(feat[col].iloc[-1])
    return float(default)

def _mean_last(feat: pd.DataFrame, col: str, n: int, default=np.nan):
    if col in feat.columns:
        s = feat[col].dropna().iloc[-n:]
        if len(s) > 0:
            return float(s.mean())
    return float(default)

def _std_last(feat: pd.DataFrame, col: str, n: int, default=np.nan):
    if col in feat.columns:
        s = feat[col].dropna().iloc[-n:]
        if len(s) > 1:
            return float(s.std())
    return float(default)

def _rank_percentile(vals: List[float]) -> List[float]:
    """同点に強いrankではなく“平均順位”→パーセンタイル。"""
    arr = np.array(vals, dtype=float)
    order = arr.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(arr), dtype=float)
    if len(arr) <= 1:
        return [1.0] * len(arr)
    return (ranks / (len(arr) - 1)).tolist()

# ─────────────────────────────────────────────────────────────────────────────
# 重み（モード/期間で切替）
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Weights:
    trend: float = 0.25        # 傾き/週足方向
    relstr: float = 0.20       # 相対強度（代替：価格モメンタム近似）
    momentum: float = 0.25     # RSI/ROC/%K の合成
    volume_sig: float = 0.10   # 出来高シグナル
    risk_ctrl: float = 0.10    # ATR/過剰ボラの抑制（減点は別で）
    supply_demand: float = 0.07# 節目/VWAP近接/ギャップ
    risk_event: float = 0.03   # 決算など（ここでは控えめ定数）

_WEIGHTS = {
    # horizon:short(〜10d)/mid(10-30d)/long(30d〜)
    # mode: aggressive / normal / defensive
    ("short", "aggressive"): Weights(trend=0.25, relstr=0.18, momentum=0.30, volume_sig=0.12, risk_ctrl=0.07, supply_demand=0.05, risk_event=0.03),
    ("short", "normal"):     Weights(),
    ("short", "defensive"):  Weights(trend=0.28, relstr=0.18, momentum=0.22, volume_sig=0.08, risk_ctrl=0.17, supply_demand=0.05, risk_event=0.02),

    ("mid", "aggressive"):   Weights(trend=0.30, relstr=0.20, momentum=0.25, volume_sig=0.12, risk_ctrl=0.08, supply_demand=0.03, risk_event=0.02),
    ("mid", "normal"):       Weights(trend=0.30, relstr=0.22, momentum=0.22, volume_sig=0.10, risk_ctrl=0.12, supply_demand=0.03, risk_event=0.01),
    ("mid", "defensive"):    Weights(trend=0.32, relstr=0.22, momentum=0.18, volume_sig=0.08, risk_ctrl=0.16, supply_demand=0.03, risk_event=0.01),

    ("long", "aggressive"):  Weights(trend=0.35, relstr=0.25, momentum=0.18, volume_sig=0.10, risk_ctrl=0.07, supply_demand=0.03, risk_event=0.02),
    ("long", "normal"):      Weights(trend=0.35, relstr=0.25, momentum=0.16, volume_sig=0.10, risk_ctrl=0.11, supply_demand=0.02, risk_event=0.01),
    ("long", "defensive"):   Weights(trend=0.36, relstr=0.26, momentum=0.15, volume_sig=0.08, risk_ctrl=0.12, supply_demand=0.02, risk_event=0.01),
}

def _pick_weights(horizon: str, mode: str) -> Weights:
    horizon = {"short":"short","mid":"mid","long":"long"}.get(horizon, "short")
    mode = {"aggressive":"aggressive","normal":"normal","defensive":"defensive"}.get(mode, "normal")
    return _WEIGHTS[(horizon, mode)]

# ─────────────────────────────────────────────────────────────────────────────
# “生”スコアの算出（重み付き合計の本体）
# ─────────────────────────────────────────────────────────────────────────────
def score_sample(feat: pd.DataFrame, mode: str = "aggressive", horizon: str = "short") -> float:
    """
    直近行までの特徴量 DataFrame を入力し、“生”スコア（負もあり得る）を返す。
    ※ 表示用の校正は score_batch でまとめて実施。
    """
    w = _pick_weights(horizon, mode)

    # 直近値を取得（欠損は安全側へ丸め）
    close = _nz(_last(feat, "close"), 0.0)
    rsi = _nz(_last(feat, "rsi14"), 50.0)
    macd_hist = _nz(_last(feat, "macd_hist"), 0.0)
    vwap_dev = _nz(_last(feat, "vwap_dev_pct"), 0.0)          # %
    ret5 = _nz(_last(feat, "ret_5d_pct"), 0.0)                # %
    slope = _nz(_last(feat, "slope_ema_fast"), 0.0)           # 方向性：>0 上昇
    atr = _nz(_last(feat, "atr"), 0.0)
    atr_pct = _nz(_last(feat, "atr_pct"), 0.0)                # %
    vol20 = _nz(_last(feat, "vol20"), 0.0)

    # 週足方向（近似）：5日移動平均の傾き符号を週足整合の代用
    slope_week_like = _nz(_mean_last(feat, "slope_ema_fast", 5), slope)

    # ─ 生の各成分（0〜1へスケーリングして重み付け） ─
    # トレンド強度：傾きをsigmoidで0〜1へ
    trend_score = 1 / (1 + math.exp(-_clip(slope, -5, 5)))    # 0..1
    trend_score = 0.5 * trend_score + 0.5 * (1 if slope_week_like >= 0 else 0)

    # 相対強度：ret5 を簡易代理（短期用）。±10%で打ち切り。
    relstr_score = (_clip(ret5, -10, 10) + 10) / 20.0         # 0..1

    # モメンタム：RSI(40〜80を中心)＋MACDヒスト符号（上昇=1, 下降=0）
    rsi_norm = (_clip(rsi, 30, 70) - 30) / 40.0               # 0..1
    macd_norm = 1.0 if macd_hist >= 0 else 0.0
    momentum_score = 0.7 * rsi_norm + 0.3 * macd_norm         # 0..1

    # 出来高シグナル：vol20 を対数圧縮。10万株で0.5, 100万株で0.8, 500万株で~0.95
    vol_score = _clip(math.log10(vol20 + 1) / 7.0, 0, 1)

    # リスクコントロール：atr_pct が低いほど良い（0%→1, 10%→0）
    risk_ctrl_score = 1.0 - _clip(atr_pct / 10.0, 0, 1)

    # 需給/節目：VWAP近接を高評価。乖離0%→1, 乖離10%→0.2
    supply_demand_score = _clip(1.0 - abs(vwap_dev) / 12.5, 0, 1)

    # リスクイベント：ここでは保守的に一定値（将来は決算/材料で動的）
    risk_event_score = 0.7

    # 重み付き合計（0..1程度に収める）
    comp = {
        "trend": trend_score,
        "relstr": relstr_score,
        "momentum": momentum_score,
        "volume_sig": vol_score,
        "risk_ctrl": risk_ctrl_score,
        "supply_demand": supply_demand_score,
        "risk_event": risk_event_score,
    }
    ww = w.__dict__
    raw01 = sum(comp[k] * ww[k] for k in comp.keys()) / sum(ww.values())

    # 出力は広いレンジを持たせる（-1..+1 目安）
    s_raw = (raw01 - 0.5) * 2.0
    return float(s_raw)


# ─────────────────────────────────────────────────────────────────────────────
# バッチ校正：points（40〜99/100）と stars（0.0〜5.0, 小数1桁）
# ─────────────────────────────────────────────────────────────────────────────
def score_batch(
    feat_map: Dict[str, pd.DataFrame],
    mode: str = "aggressive",
    horizon: str = "short",
) -> Dict[str, Dict]:
    """
    feat_map: { code: features_df }
    返り値: { code: {'raw':..., 'points':..., 'stars':..., 'extras':{...}} }
    """
    codes = list(feat_map.keys())
    raws = []
    # まず“生”スコア
    for c in codes:
        try:
            raws.append(score_sample(feat_map[c], mode=mode, horizon=horizon))
        except Exception:
            raws.append(-9e9)  # 壊れ値で最下位扱い

    # パーセンタイル（相対校正）
    ps = _rank_percentile(raws)

    out: Dict[str, Dict] = {}

    # α（上位圧縮）設定
    alpha = {"aggressive": 1.25, "normal": 1.35, "defensive": 1.5}.get(mode, 1.35)

    for code, s_raw, p in zip(codes, raws, ps):
        feat = feat_map[code]
        close = _nz(_last(feat, "close"), 0.0)
        vwap_dev = _nz(_last(feat, "vwap_dev_pct"), 0.0)        # %
        atr = _nz(_last(feat, "atr"), 0.0)
        atr_pct = _nz(_last(feat, "atr_pct"), 0.0)
        ret5 = _nz(_last(feat, "ret_5d_pct"), 0.0)
        vol20 = _nz(_last(feat, "vol20"), 0.0)
        vol_yen20 = close * vol20

        # 乖離/過熱/流動性/ボラの減点（門前で上振れを潰す）
        penalties = 0
        if vol_yen20 < 50_000_000:                 # < 5,000万円/日
            penalties += 10
        if abs(vwap_dev) > 10:
            penalties += 12
        elif abs(vwap_dev) > 6:
            penalties += 6
        if atr_pct > 8:
            penalties += 15
        elif atr_pct > 6:
            penalties += 8
        if ret5 > 12:                               # 急騰後の過熱入り
            penalties += 12

        # 基本点
        pts0 = int(round(100 * _clip(p, 0, 1)))
        points = pts0 - penalties
        points = int(_clip(points, 40, 100))

        # 原則は 99 点上限
        allow_100 = (
            p >= 0.995
            and penalties == 0
            and vol_yen20 >= 300_000_000          # 3億/日 以上
            and abs(vwap_dev) <= 6
            and atr_pct <= 5
        )
        if not allow_100 and points >= 100:
            points = 99

        # ─ 信頼度（学習の代替：安定性＋リスク距離） ─
        # 近傍20本の変動で安定性を近似
        rsi_std = _std_last(feat, "rsi14", 20, 8.0)       # 小さいほど良
        slope_std = _std_last(feat, "slope_ema_fast", 20, 0.5)
        # 標準化して 0..1 へ反転
        stbl = 1.0 - _clip((rsi_std / 15.0) * 0.6 + (abs(slope_std) / 2.0) * 0.4, 0, 1)

        # リスク距離（ATRに対して適切か）… atr_pct が低いほど良
        risk_dist = 1.0 - _clip(atr_pct / 10.0, 0, 1)

        # conf_raw：0..1
        conf_raw = _clip(0.6 * stbl + 0.4 * risk_dist, 0, 1)
        # 非線形圧縮（上位を詰める）
        stars_val = round(_clip(5 * (conf_raw ** alpha), 0, 5), 1)

        # ⭐️5 ゲート（ぜんぶ満たした時だけ 5.0）
        gate_ok = (
            p >= 0.985 and
            vol_yen20 >= 100_000_000 and
            abs(vwap_dev) <= 6 and
            atr_pct <= 5
        )
        if stars_val >= 5.0 and not gate_ok:
            stars_val = 4.9

        out[code] = {
            "raw": float(s_raw),
            "points": int(points),
            "stars": float(stars_val),
            "extras": {
                "percentile": float(round(p, 4)),
                "penalties": int(penalties),
                "vol_yen20": float(vol_yen20),
                "atr_pct": float(atr_pct),
                "vwap_dev_pct": float(vwap_dev),
                "ret_5d_pct": float(ret5),
            },
        }

    return out