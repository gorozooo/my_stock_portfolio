# -*- coding: utf-8 -*-
"""
aiapp.services.picks_filter

「どの銘柄をそもそも AI ピックの土俵に上げるか」を決めるフィルタレイヤー。

役割は 2 段構え：

1) ユニバースフィルタ（銘柄マスタベース）
   - あまりにも流動性が低い銘柄 / 極端に小さい銘柄を最初から除外
   - 今後、上場区分・信用規制 等もここで判定できるようにする

2) ポストフィルタ（特徴量ベース）
   - Price / ATR / Volume / MA20 など「実際のマーケットデータ」を見て
     仕手株っぽい極端な銘柄を除外する
   - 「スコアは高いけどボラがエグすぎる」みたいなケースをここで落とす

※ このモジュールでは「採用 / 除外」の判定だけを行い、
   スコアリングや理由テキストの生成ロジックとは分離する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    # StockMaster が存在しない環境でも import error で死なないようにしておく
    from aiapp.models import StockMaster  # type: ignore
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore


# =========================================================
# 設定クラス
# =========================================================

@dataclass
class UniverseFilterConfig:
    """
    ユニバースフィルタ（銘柄マスタベース）の設定。

    ・ここでは「そもそも候補に入れたくない銘柄」を落とす。
    ・主に長期的な属性（時価総額・平均売買代金・株価水準など）で判定。
    """

    # --- 価格・規模系 ---
    min_price: float = 300.0            # これ未満の株価はユニバースから除外（超低位株NG）
    min_market_cap: float = 20_000_000_000.0  # これ未満の時価総額は除外（20億円〜）

    # --- 流動性系 ---
    # 1日あたりの平均売買代金（20日など）で判定
    min_avg_trading_value: float = 50_000_000.0  # 5,000万/日 未満は除外

    # --- 市場・区分系 ---
    # None の場合は市場フィルタを行わない
    allowed_markets: Optional[Sequence[str]] = None  # 例: ["プライム", "スタンダード"]

    # 今後、信用規制や業種NGなどを足す場合はここに項目を追加する


@dataclass
class PostFilterConfig:
    """
    ポストフィルタ（特徴量ベース）の設定。

    ・実際のマーケットデータ（ATR、出来高倍率）を使って
      「仕手株っぽくて危険すぎる銘柄」を除外する。
    """

    # ATR ベースのボラティリティ制限（%）
    # 例: ATR が株価の 15% を超えるような銘柄は除外
    max_atr_pct: float = 15.0

    # 出来高 / MA20 の上限（あまりにも異常に高い出来高は仕手っぽいので除外）
    # 例: 50倍を超えたら仕手認定して除外
    max_volume_ma20_ratio: float = 50.0

    # 価格の下限（ここでも一応チェックしておく）
    min_price: float = 300.0

    # スコアの下限（0〜100）。ここであまりにも低いスコアを切り捨ててもよい。
    min_score_100: int = 0


@dataclass
class PostFilterResult:
    """ポストフィルタの判定結果。"""

    accept: bool
    reason: Optional[str] = None  # 除外した場合の理由テキスト（ログ・検証用）


# =========================================================
# 内部ユーティリティ
# =========================================================

def _safe_number(x: Any) -> Optional[float]:
    """数値に変換できれば float を、できなければ None を返す。"""
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _normalize_code(code: Any) -> str:
    """銘柄コードをゼロ埋め無しの文字列に正規化。"""
    if code is None:
        return ""
    s = str(code).strip()
    return s


# =========================================================
# 1) ユニバースフィルタ（銘柄マスタベース）
# =========================================================

def filter_universe_codes(
    codes: Iterable[str],
    cfg: Optional[UniverseFilterConfig] = None,
) -> List[str]:
    """
    銘柄コード一覧（Universe）から、
    「超低位株・極端に小さい銘柄・流動性が低すぎる銘柄」などを除外する。

    戻り値: フィルタを通過した銘柄コードのリスト。

    ※ StockMaster が利用できない環境では、フィルタを行わずそのまま返す。
    """
    cfg = cfg or UniverseFilterConfig()
    codes = [_normalize_code(c) for c in codes if c]

    if not codes:
        return []

    if StockMaster is None:
        # マスタが無い場合は何もフィルタせず返す
        return list(codes)

    # マスタから対象銘柄を引く
    qs = StockMaster.objects.filter(code__in=codes)

    # 想定フィールド名:
    #   - last_price      : 直近株価
    #   - market_cap      : 時価総額
    #   - avg_trading_value_20d など: 平均売買代金
    #   - market          : 市場区分（"プライム" など）
    #
    # 実際のフィールド名が異なる場合は、必要に応じてここを調整する。
    pass_codes: List[str] = []

    for m in qs:
        code = _normalize_code(getattr(m, "code", None))

        price = _safe_number(
            getattr(m, "last_price", None)
            or getattr(m, "close_price", None)
            or getattr(m, "price", None)
        )
        mcap = _safe_number(getattr(m, "market_cap", None))
        avg_val = _safe_number(
            getattr(m, "avg_trading_value_20d", None)
            or getattr(m, "avg_value_20d", None)
            or getattr(m, "avg_trading_value", None)
        )
        market = getattr(m, "market", None) or getattr(m, "market_segment", None)

        # --- 価格・時価総額・流動性のしきい値チェック ---
        if price is not None and price < cfg.min_price:
            # 超低位株 → 除外
            continue

        if mcap is not None and mcap < cfg.min_market_cap:
            # 規模が小さすぎる銘柄 → 除外
            continue

        if avg_val is not None and avg_val < cfg.min_avg_trading_value:
            # 売買代金が低すぎる銘柄 → 除外
            continue

        # --- 市場区分フィルタ ---
        if cfg.allowed_markets:
            if market is not None:
                mk = str(market).strip()
                if mk not in cfg.allowed_markets:
                    # 対象外市場 → 除外
                    continue
            # market 情報が無い場合は一旦通す（厳格にしたければここで落としてもよい）

        pass_codes.append(code)

    # マスタに存在しなかった銘柄は、一旦そのまま通す（必要に応じて落としてもOK）
    known = set(pass_codes)
    for c in codes:
        if c not in known:
            pass_codes.append(c)

    return pass_codes


# =========================================================
# 2) ポストフィルタ（特徴量ベース）
# =========================================================

def post_filter_pick(
    code: str,
    feat_last: Dict[str, Any],
    *,
    last_close: Optional[float],
    atr: Optional[float],
    score_100: Optional[int],
    cfg: Optional[PostFilterConfig] = None,
) -> PostFilterResult:
    """
    1銘柄ごとの「最終チェック」。

    ・特徴量の最終行 `feat_last`（make_features(...).iloc[-1].to_dict() 相当）と
      last_close / atr / score_100 を元に、
      「仕手株すぎる / ボラがエグすぎる / 出来高が異常」などを判定する。

    戻り値:
        PostFilterResult(accept=True)   → 採用
        PostFilterResult(accept=False)  → 除外（reason に理由を入れる）
    """
    cfg = cfg or PostFilterConfig()
    code_str = _normalize_code(code)

    # --------------------
    # 価格の下限チェック
    # --------------------
    if last_close is not None and last_close < cfg.min_price:
        return PostFilterResult(
            accept=False,
            reason=f"{code_str}: 直近株価が {cfg.min_price:.0f} 円未満のため除外（超低位株）。",
        )

    # --------------------
    # ATR ベースのボラティリティチェック
    # --------------------
    atr_val = _safe_number(atr)
    price_val = _safe_number(last_close)

    if atr_val is not None and price_val is not None and price_val > 0:
        atr_pct = (atr_val / price_val) * 100.0
        if atr_pct > cfg.max_atr_pct:
            return PostFilterResult(
                accept=False,
                reason=(
                    f"{code_str}: ATR が株価の約 {atr_pct:.1f}% と大きく、"
                    f"ボラティリティが {cfg.max_atr_pct:.1f}% を超えているため除外。"
                ),
            )

    # --------------------
    # 出来高 / MA20 の異常値チェック
    # --------------------
    vol = _safe_number(feat_last.get("Volume"))
    ma20 = _safe_number(feat_last.get("MA20"))

    if vol is not None and ma20 is not None and ma20 > 0:
        vol_ratio = vol / ma20  # 何倍か
        if vol_ratio > cfg.max_volume_ma20_ratio:
            return PostFilterResult(
                accept=False,
                reason=(
                    f"{code_str}: 出来高が20日平均の約 {vol_ratio:.1f}倍と極端に高く、"
                    f"仕手株的な動きと判断して除外。"
                ),
            )

    # --------------------
    # スコアの下限チェック（必要なら有効化）
    # --------------------
    if score_100 is not None and score_100 < cfg.min_score_100:
        return PostFilterResult(
            accept=False,
            reason=(
                f"{code_str}: スコア {score_100} 点が下限 {cfg.min_score_100} 点を下回るため除外。"
            ),
        )

    # すべてのチェックを通過 → 採用
    return PostFilterResult(accept=True, reason=None)


# =========================================================
# 3) 将来用のまとめヘルパ（まだ使わないが形だけ用意）
# =========================================================

def filter_universe_and_log(
    codes: Iterable[str],
    cfg_universe: Optional[UniverseFilterConfig] = None,
) -> Tuple[List[str], List[str]]:
    """
    ユニバースフィルタをかけた結果と、落とした銘柄の簡易ログメッセージを返すヘルパ。

    戻り値:
        ( kept_codes, dropped_logs )
    """
    cfg_universe = cfg_universe or UniverseFilterConfig()
    codes = [_normalize_code(c) for c in codes if c]

    if not codes:
        return [], []

    kept = filter_universe_codes(codes, cfg_universe)

    dropped_logs: List[str] = []
    dropped_set = set(codes) - set(kept)
    for c in sorted(dropped_set):
        dropped_logs.append(f"{c}: UniverseFilter により除外（価格・規模・流動性などの条件を満たさず）")

    return kept, dropped_logs