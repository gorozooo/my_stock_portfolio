# aiapp/services/policy_build/build_service.py
# -*- coding: utf-8 -*-
"""
【このファイルは何？】
policy_build の“本体サービス”。
fundamentals（市場データ）と policy_news（政策/社会情勢ニュース）と seed（手動ベース）を合成して、
33業種それぞれの「その日の方針スコア（policy_score）」を JSON に保存する。

目的（B案：将来拡張前提）
- 係数テーブル（dict）で “市場→中間スコア（fx/risk/us_rates/jp_rates）” を作る
- 33業種すべてに sector_weight を定義し、セクターごとに効き方を変える
- ログ用に reason（なぜ増減したか）を sector_rows[].meta に保存する
- input_policy.json（手動seed）は “ベース” として残しつつ、上書きではなく「上乗せ」する
- 追加：policy_news_build の結果（ニュース要因）も合流し、delta_news と reasons に反映する

入力:
- media/aiapp/fundamentals/latest_fundamentals.json
- media/aiapp/policy/input_policy.json（任意：手動seed）
- media/aiapp/policy_news/latest_policy_news.json（任意：ニュースseed/要因）

出力:
- media/aiapp/policy/latest_policy.json
- media/aiapp/policy/{timestamp}_policy.json

補足（今回の修正）
- seed に混ざる「(仮) / （仮） / (temporary)」などの“仮ラベル”は policy_build 側で除去して出力する。
  → hybrid が flags/reasons を拾っても、UIに(仮)が残らないようにする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# policy_news（ニュース材料）
from aiapp.services.policy_news.repo import load_policy_news_snapshot

JST = timezone(timedelta(hours=9))

POLICY_DIR = Path("media/aiapp/policy")
FUND_DIR = Path("media/aiapp/fundamentals")

INPUT_POLICY = POLICY_DIR / "input_policy.json"
LATEST_POLICY = POLICY_DIR / "latest_policy.json"
LATEST_FUND = FUND_DIR / "latest_fundamentals.json"


# =========================
# 係数テーブル（将来拡張用）
# =========================
COEFS: Dict[str, Any] = {
    # components の基本係数
    "fx_per_pct": 0.8,            # USDJPY change_pct * 0.8
    "risk_per_pct": -0.5,         # DXY change_pct * -0.5（ドル高=リスクオフ寄り）
    "us_rates_per_pct": -0.3,     # ^TNX change_pct * -0.3（米金利上昇=グロース逆風）
    "jp_rates_per_level": -0.6,   # (JGB10Y - baseline) * -0.6（水準ベース）
    "jp_rates_baseline": 1.0,     # 1.0% を基準にする

    # clamp（暴れ防止）
    "component_clamp": (-3.0, 3.0),   # fx/risk/us_rates/jp_rates 各component
    "sector_delta_clamp": (-6.0, 6.0),# セクターに合成した “上乗せ分”
    "policy_score_clamp": (-10.0, 10.0), # 最終policy_score（seed + delta）

    # news 合流（policy_news）
    # policy_news は build 時点で「factors_sum（fx/rates/risk）」と「sector_sum（セクター別delta）」を持つ前提。
    # - sector_sum を優先して delta_news に反映
    # - factors_sum も「セクターweight」で増幅/減衰して delta_news に少し足す（将来拡張しやすくする）
    #   ※ policy_news の "rates" はここでは jp_rates に寄せる（日本金利・政策寄りの扱い）
    "news_factor_k": 0.50,        # news の factors_sum を混ぜる強さ（小さめ）
    "news_delta_clamp": (-6.0, 6.0),
}


# =========================
# 33業種 フル weight テーブル
# =========================
# factors: fx / risk / us_rates / jp_rates
SECTOR_WEIGHTS_33: Dict[str, Dict[str, float]] = {
    "水産・農林業":        {"fx": 0.2, "risk": 0.2,  "us_rates": 0.1,  "jp_rates": 0.1},
    "鉱業":               {"fx": 0.5, "risk": -0.3, "us_rates": -0.2, "jp_rates": -0.1},
    "建設業":             {"fx": 0.2, "risk": -0.2, "us_rates": -0.3, "jp_rates": -0.5},
    "食料品":             {"fx": 0.1, "risk": 0.4,  "us_rates": 0.1,  "jp_rates": 0.1},
    "繊維製品":           {"fx": 0.5, "risk": -0.3, "us_rates": -0.1, "jp_rates": -0.1},
    "パルプ・紙":         {"fx": 0.3, "risk": -0.2, "us_rates": -0.1, "jp_rates": -0.1},
    "化学":               {"fx": 0.6, "risk": -0.4, "us_rates": -0.3, "jp_rates": -0.2},
    "医薬品":             {"fx": 0.1, "risk": 0.8,  "us_rates": 0.2,  "jp_rates": 0.1},

    "石油・石炭製品":     {"fx": 0.6, "risk": -0.3, "us_rates": -0.2, "jp_rates": -0.1},
    "ゴム製品":           {"fx": 0.7, "risk": -0.4, "us_rates": -0.2, "jp_rates": -0.1},
    "ガラス・土石製品":   {"fx": 0.3, "risk": -0.2, "us_rates": -0.2, "jp_rates": -0.2},
    "鉄鋼":               {"fx": 0.7, "risk": -0.5, "us_rates": -0.3, "jp_rates": -0.1},
    "非鉄金属":           {"fx": 0.7, "risk": -0.5, "us_rates": -0.3, "jp_rates": -0.1},
    "金属製品":           {"fx": 0.5, "risk": -0.3, "us_rates": -0.2, "jp_rates": -0.2},
    "機械":               {"fx": 0.9, "risk": -0.6, "us_rates": -0.4, "jp_rates": -0.2},
    "電気機器":           {"fx": 1.1, "risk": -0.7, "us_rates": -0.6, "jp_rates": -0.2},
    "輸送用機器":         {"fx": 1.4, "risk": -0.6, "us_rates": -0.3, "jp_rates": -0.2},
    "精密機器":           {"fx": 0.8, "risk": -0.7, "us_rates": -0.7, "jp_rates": -0.2},
    "その他製品":         {"fx": 0.4, "risk": -0.3, "us_rates": -0.2, "jp_rates": -0.1},

    "電気・ガス業":       {"fx": 0.0, "risk": 0.6,  "us_rates": 0.2,  "jp_rates": 0.3},
    "陸運業":             {"fx": 0.1, "risk": 0.4,  "us_rates": 0.1,  "jp_rates": -0.1},
    "海運業":             {"fx": 1.0, "risk": -0.6, "us_rates": -0.3, "jp_rates": -0.1},
    "空運業":             {"fx": 0.4, "risk": -0.4, "us_rates": -0.2, "jp_rates": -0.1},
    "倉庫・運輸関連業":   {"fx": 0.3, "risk": -0.2, "us_rates": -0.1, "jp_rates": -0.1},

    "情報・通信業":       {"fx": 0.2, "risk": -0.5, "us_rates": -0.8, "jp_rates": -0.3},
    "卸売業":             {"fx": 0.4, "risk": -0.3, "us_rates": -0.2, "jp_rates": -0.1},
    "小売業":             {"fx": 0.2, "risk": -0.2, "us_rates": -0.1, "jp_rates": -0.2},

    "銀行業":             {"fx": 0.1, "risk": -0.2, "us_rates": 0.6,  "jp_rates": 1.6},
    "証券、商品先物取引業":{"fx": 0.2, "risk": -0.5, "us_rates": 0.2,  "jp_rates": 0.4},
    "保険業":             {"fx": 0.2, "risk": -0.3, "us_rates": 0.4,  "jp_rates": 0.8},
    "その他金融業":       {"fx": 0.2, "risk": -0.4, "us_rates": 0.2,  "jp_rates": 0.4},

    "不動産業":           {"fx": 0.1, "risk": -0.2, "us_rates": -0.6, "jp_rates": -1.6},
    "サービス業":         {"fx": 0.2, "risk": -0.3, "us_rates": -0.4, "jp_rates": -0.2},
}


# =========================
# ユーティリティ
# =========================
def _dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _clamp(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if v != v:
        return None
    return max(lo, min(hi, v))


def _norm_key(s: str) -> str:
    """
    セクター名やキー照合用の正規化。
    “不可視文字”が混ざっても照合できるようにする。
    """
    if s is None:
        return ""
    t = str(s)

    # よくある不可視/制御系を削る
    bad = [
        "\u200b", "\u200c", "\u200d", "\ufeff",
        "\u2060", "\u00ad",
    ]
    for b in bad:
        t = t.replace(b, "")

    # 全角スペース/半角スペース/改行タブ除去
    t = t.replace("\u3000", " ")
    t = " ".join(t.split())
    return t.strip()


def _clean_label(s: Any) -> str:
    """
    seed由来の flags / reasons に残りがちな “仮ラベル”を除去する。
    - (仮), （仮）, (temporary), temporary を削る
    - 余分な空白を正規化
    """
    t = str(s or "")
    for pat in ["(仮)", "（仮）", "(temporary)", "temporary"]:
        t = t.replace(pat, "")
    t = " ".join(t.split())
    return t.strip()


def _extract_fund_asof_date(fund: Dict[str, Any]) -> str:
    """
    fundamentals の meta.asof（ISO: 2026-01-15T...）から YYYY-MM-DD を作る。
    取れない場合は “今日(JST)”。
    """
    iso = None
    try:
        iso = (fund.get("meta") or {}).get("asof")
    except Exception:
        iso = None

    if isinstance(iso, str) and len(iso) >= 10:
        return iso[:10]
    return datetime.now(JST).strftime("%Y-%m-%d")


def _get_series_item(fund: Dict[str, Any], sym: str) -> Dict[str, Any]:
    mc = fund.get("market_context") or {}
    series = mc.get("series") or {}
    v = series.get(sym)
    return v if isinstance(v, dict) else {}


def _build_market_inputs(fund: Dict[str, Any]) -> Dict[str, Any]:
    """
    policy_build が参照する “材料” を抜き出す（欠損でも落とさない）。
    """
    inputs: Dict[str, Any] = {}

    # 為替/ドル
    inputs["usd_jpy_pct"] = _safe_float(_get_series_item(fund, "USDJPY=X").get("change_pct"))
    inputs["dxy_pct"] = _safe_float(_get_series_item(fund, "DX-Y.NYB").get("change_pct"))

    # 金利（米/日）
    inputs["tnx_pct"] = _safe_float(_get_series_item(fund, "^TNX").get("change_pct"))

    jgb_item = _get_series_item(fund, "JGB10Y=RR")
    inputs["jgb10y_last"] = _safe_float(jgb_item.get("last"))
    inputs["jgb10y_source"] = jgb_item.get("source")

    # 日経（参考）
    inputs["n225_pct"] = _safe_float(_get_series_item(fund, "^N225").get("change_pct"))
    inputs["nif_pct"] = _safe_float(_get_series_item(fund, "NIY=F").get("change_pct"))

    # errors（取得失敗の可視化）
    mc = fund.get("market_context") or {}
    errs = mc.get("errors") or {}
    inputs["fund_errors"] = errs if isinstance(errs, dict) and errs else None

    notes = mc.get("notes") or {}
    inputs["fund_notes"] = notes if isinstance(notes, dict) and notes else None

    return inputs


def _compute_components(inputs: Dict[str, Any]) -> Tuple[Dict[str, float], List[str]]:
    """
    市場→中間スコア（fx/risk/us_rates/jp_rates）。
    戻り:
      components: dict
      warnings: list[str]
    """
    warnings: List[str] = []

    lo_c, hi_c = COEFS["component_clamp"]

    usd_jpy_pct = inputs.get("usd_jpy_pct")
    dxy_pct = inputs.get("dxy_pct")
    tnx_pct = inputs.get("tnx_pct")
    jgb10y_last = inputs.get("jgb10y_last")

    fx = None
    if usd_jpy_pct is not None:
        fx = _clamp(usd_jpy_pct * float(COEFS["fx_per_pct"]), lo_c, hi_c)
    else:
        warnings.append("missing:USDJPY=X.change_pct")

    risk = None
    if dxy_pct is not None:
        risk = _clamp(dxy_pct * float(COEFS["risk_per_pct"]), lo_c, hi_c)
    else:
        warnings.append("missing:DX-Y.NYB.change_pct")

    us_rates = None
    if tnx_pct is not None:
        us_rates = _clamp(tnx_pct * float(COEFS["us_rates_per_pct"]), lo_c, hi_c)
    else:
        warnings.append("missing:^TNX.change_pct")

    jp_rates = None
    if jgb10y_last is not None:
        base = float(COEFS["jp_rates_baseline"])
        jp_rates = _clamp((jgb10y_last - base) * float(COEFS["jp_rates_per_level"]), lo_c, hi_c)
    else:
        warnings.append("missing:JGB10Y=RR.last")

    # 欠損は 0 として扱う（落とさない）
    comp: Dict[str, float] = {
        "fx": float(fx) if fx is not None else 0.0,
        "risk": float(risk) if risk is not None else 0.0,
        "us_rates": float(us_rates) if us_rates is not None else 0.0,
        "jp_rates": float(jp_rates) if jp_rates is not None else 0.0,
    }
    return comp, warnings


def _sector_reason_lines(
    sector: str,
    weights: Dict[str, float],
    comps: Dict[str, float],
    inputs: Dict[str, Any],
) -> Tuple[List[str], List[str], Dict[str, float]]:
    """
    セクターの理由文と flags を作る。
    - lines: 1行理由（複数）
    - flags: UI向け短いタグ
    - comp_detail: どの要因が何点効いたか
    """
    lines: List[str] = []
    flags: List[str] = []
    comp_detail: Dict[str, float] = {}

    # 各要因の寄与（component * weight）
    for k in ("fx", "risk", "us_rates", "jp_rates"):
        w = float(weights.get(k, 0.0))
        c = float(comps.get(k, 0.0))
        comp_detail[k] = c * w

    # FX（円安/円高）
    usd_jpy_pct = inputs.get("usd_jpy_pct")
    if usd_jpy_pct is not None:
        if usd_jpy_pct > 0.2:
            lines.append("USDJPYが上向き（円安方向）")
        elif usd_jpy_pct < -0.2:
            lines.append("USDJPYが下向き（円高方向）")

        if weights.get("fx", 0.0) >= 0.7:
            if usd_jpy_pct > 0.2:
                flags.append("円安追い風")
            elif usd_jpy_pct < -0.2:
                flags.append("円高逆風")

    # DXY（ざっくり risk-off 方向）
    dxy_pct = inputs.get("dxy_pct")
    if dxy_pct is not None:
        if dxy_pct > 0.2:
            lines.append("DXYが上向き（リスクオフ寄り）")
        elif dxy_pct < -0.2:
            lines.append("DXYが下向き（リスクオン寄り）")

        if weights.get("risk", 0.0) >= 0.4 and dxy_pct > 0.2:
            flags.append("リスクオフ耐性")
        if weights.get("risk", 0.0) <= -0.4 and dxy_pct > 0.2:
            flags.append("リスクオフ逆風")

    # 米金利
    tnx_pct = inputs.get("tnx_pct")
    if tnx_pct is not None:
        if tnx_pct > 0.2:
            lines.append("米金利（^TNX）が上向き")
        elif tnx_pct < -0.2:
            lines.append("米金利（^TNX）が下向き")

        if weights.get("us_rates", 0.0) <= -0.6 and tnx_pct > 0.2:
            flags.append("米金利上昇逆風")

    # 日金利（JGB10Y 水準）
    jgb10y_last = inputs.get("jgb10y_last")
    if jgb10y_last is not None:
        if jgb10y_last >= 1.5:
            lines.append(f"日本10年金利（JGB10Y）が高め（{jgb10y_last:.2f}%）")
        elif jgb10y_last <= 0.7:
            lines.append(f"日本10年金利（JGB10Y）が低め（{jgb10y_last:.2f}%）")

        if weights.get("jp_rates", 0.0) <= -1.2 and jgb10y_last >= 1.5:
            flags.append("国内金利高逆風")
        if weights.get("jp_rates", 0.0) >= 1.2 and jgb10y_last >= 1.5:
            flags.append("金利追い風")

    # セクター名を先頭に付けた短いまとめ（最後に1行）
    ranked = sorted(comp_detail.items(), key=lambda kv: abs(float(kv[1])), reverse=True)
    top = [f"{k}:{v:+.2f}" for k, v in ranked[:2]]
    if top:
        lines.append(f"{sector} 主要寄与: " + " / ".join(top))

    def uniq(xs: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for x in xs:
            if x in seen:
                continue
            out.append(x)
            seen.add(x)
        return out

    # flags/lines はここで必ずクリーン化して返す（将来seed混入でもUIが汚れない）
    return (
        uniq([_clean_label(x) for x in lines if _clean_label(x)]),
        uniq([_clean_label(x) for x in flags if _clean_label(x)]),
        comp_detail,
    )


def _news_sector_delta(
    sector_norm: str,
    weights: Dict[str, float],
    news_sector_sum: Dict[str, float],
    news_factors_sum: Dict[str, float],
) -> Tuple[float, Dict[str, float]]:
    """
    policy_news の結果から delta_news を作る（将来拡張しやすい形）。

    ルール（現時点）:
    - sector_sum[sector] を主役として採用（ニュースが「どのセクターに効くか」）
    - さらに factors_sum（fx/risk/rates）を sector_weight で増幅/減衰して少し足す
      * policy_news の "rates" は、ここでは jp_rates として扱う（日本政策・金利寄りの影響）
    """
    base = float(news_sector_sum.get(sector_norm, 0.0) or 0.0)

    # factors_sum の混ぜ（小さめ）
    k = float(COEFS.get("news_factor_k", 0.5))
    fx = float(news_factors_sum.get("fx", 0.0) or 0.0)
    risk = float(news_factors_sum.get("risk", 0.0) or 0.0)
    rates = float(news_factors_sum.get("rates", 0.0) or 0.0)

    w_fx = float(weights.get("fx", 0.0) or 0.0)
    w_risk = float(weights.get("risk", 0.0) or 0.0)
    w_jp = float(weights.get("jp_rates", 0.0) or 0.0)

    mixed = (fx * w_fx) + (risk * w_risk) + (rates * w_jp)
    delta = base + (mixed * k)

    lo_n, hi_n = COEFS["news_delta_clamp"]
    delta2 = _clamp(delta, lo_n, hi_n)
    if delta2 is None:
        delta2 = 0.0

    detail = {
        "news_base_sector": float(base),
        "news_mixed_factor": float(mixed),
        "news_factor_k": float(k),
        "news_fx": float(fx),
        "news_risk": float(risk),
        "news_rates": float(rates),
        "w_fx": float(w_fx),
        "w_risk": float(w_risk),
        "w_jp_rates": float(w_jp),
    }
    return float(delta2), detail


# =========================
# 生成結果スキーマ
# =========================
@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, Any]
    meta: Dict[str, Any]


# =========================
# メイン処理
# =========================
def build_policy_snapshot() -> PolicySnapshot:
    """
    B案のpolicy生成:
    - seed（input_policy.json）を読み、policy_score/flags をベースとして残す
    - fundamentals 由来の中間スコア（fx/risk/us_rates/jp_rates）を作る
    - 33業種すべてに sector_weight を適用し “上乗せ分delta_market” を作る
    - policy_news 由来の “delta_news” を加算する（sector_sum + factors_sum*weights）
    - reasons（内訳/材料）を sector_rows[].meta に保存
    """
    POLICY_DIR.mkdir(parents=True, exist_ok=True)

    seed = _safe_json_load(INPUT_POLICY)
    seed_rows = seed.get("sector_rows") if isinstance(seed.get("sector_rows"), dict) else {}
    seed_meta = seed.get("meta") if isinstance(seed.get("meta"), dict) else {}

    fund = _safe_json_load(LATEST_FUND)
    asof = _extract_fund_asof_date(fund)

    # 材料抽出（市場）
    inputs = _build_market_inputs(fund)
    comps, warnings = _compute_components(inputs)

    # policy_news（ニュース材料）をロード（欠損でも落とさない）
    news_snap = load_policy_news_snapshot()
    news_asof = getattr(news_snap, "asof", None)
    news_items = getattr(news_snap, "items", None) or []
    news_meta = getattr(news_snap, "meta", None) or {}
    news_factors_sum = getattr(news_snap, "factors_sum", None) or {}
    news_sector_sum_raw = getattr(news_snap, "sector_sum", None) or {}

    # セクターキー照合（不可視対策）
    weights_norm = {_norm_key(k): v for k, v in SECTOR_WEIGHTS_33.items()}

    seed_norm_map: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for k, v in seed_rows.items():
        if not isinstance(v, dict):
            continue
        seed_norm_map[_norm_key(k)] = (k, v)

    # news の sector_sum も正規化して合わせる
    news_sector_sum: Dict[str, float] = {}
    if isinstance(news_sector_sum_raw, dict):
        for k, v in news_sector_sum_raw.items():
            nk = _norm_key(k)
            fv = _safe_float(v)
            if nk:
                news_sector_sum[nk] = float(fv) if fv is not None else 0.0

    # 全33業種の出力を作る
    lo_d, hi_d = COEFS["sector_delta_clamp"]
    lo_p, hi_p = COEFS["policy_score_clamp"]

    out_rows: Dict[str, Any] = {}
    delta_market_stats: List[float] = []
    delta_news_stats: List[float] = []
    delta_total_stats: List[float] = []

    for sector_display_norm, w in weights_norm.items():
        sector_display = sector_display_norm  # 正規化後を表示名にする（変な空白を落とす）

        # seed（あれば）
        seed_key, seed_row = seed_norm_map.get(sector_display_norm, (None, None))
        seed_score = None
        seed_flags: List[str] = []
        seed_why = None

        if isinstance(seed_row, dict):
            seed_score = _safe_float(seed_row.get("policy_score"))
            fs = seed_row.get("flags")
            if isinstance(fs, list):
                # seed flags から “仮” を除去して保存
                seed_flags = [_clean_label(str(x)) for x in fs if _clean_label(str(x))]
            m = seed_row.get("meta")
            if isinstance(m, dict):
                seed_why = _clean_label(m.get("why"))

        # reasons / flags / 内訳（市場）
        reason_lines_market, gen_flags_market, comp_detail_market = _sector_reason_lines(
            sector=sector_display,
            weights=w,
            comps=comps,
            inputs=inputs,
        )

        # delta_market（市場の上乗せ）
        delta_market_raw = (
            comps["fx"] * float(w.get("fx", 0.0))
            + comps["risk"] * float(w.get("risk", 0.0))
            + comps["us_rates"] * float(w.get("us_rates", 0.0))
            + comps["jp_rates"] * float(w.get("jp_rates", 0.0))
        )
        delta_market = _clamp(delta_market_raw, lo_d, hi_d)
        delta_market_val = float(delta_market) if delta_market is not None else 0.0
        delta_market_stats.append(delta_market_val)

        # delta_news（ニュースの上乗せ）
        # - itemsが0でも factors_sum は0のはずなので、結果は0に落ちる
        delta_news_val, delta_news_detail = _news_sector_delta(
            sector_norm=sector_display_norm,
            weights=w,
            news_sector_sum=news_sector_sum,
            news_factors_sum=news_factors_sum if isinstance(news_factors_sum, dict) else {},
        )
        delta_news_stats.append(float(delta_news_val))

        # 最終スコア（seed + delta_market + delta_news）
        base = float(seed_score) if seed_score is not None else 0.0
        delta_total_raw = base + delta_market_val + float(delta_news_val)
        final_score = _clamp(delta_total_raw, lo_p, hi_p)
        final_score_val = float(final_score) if final_score is not None else 0.0
        delta_total_stats.append(float(final_score_val - base))

        # flags は seed + generated を合成（重複削除）
        flags_all: List[str] = []
        for x in seed_flags + gen_flags_market:
            x2 = _clean_label(x)
            if not x2:
                continue
            if x2 in flags_all:
                continue
            flags_all.append(x2)

        # reasons は「市場→ニュース」の順で入れる（見た時に理解しやすい）
        reasons_all: List[str] = []
        for x in reason_lines_market:
            x2 = _clean_label(x)
            if x2 and x2 not in reasons_all:
                reasons_all.append(x2)

        # news由来の reasons（policy_news.items の要約を軽く入れる）
        # - sectorに対して効いた items を最大3つだけ足す（長くしない）
        news_lines: List[str] = []
        try:
            if isinstance(news_items, list) and news_items:
                hit = []
                for it in news_items:
                    if not isinstance(it, dict):
                        continue
                    sectors = it.get("sectors")
                    if not isinstance(sectors, list):
                        continue
                    # sectors 側も正規化して比較
                    sec_norms = [_norm_key(s) for s in sectors]
                    if sector_display_norm in sec_norms:
                        title = it.get("title") or it.get("id") or "news_item"
                        fxv = it.get("factors", {}).get("fx") if isinstance(it.get("factors"), dict) else None
                        rv = it.get("factors", {}).get("risk") if isinstance(it.get("factors"), dict) else None
                        pv = it.get("factors", {}).get("rates") if isinstance(it.get("factors"), dict) else None
                        hit.append((_clean_label(str(title)), _safe_float(fxv), _safe_float(pv), _safe_float(rv)))
                for title, fxv, pv, rv in hit[:3]:
                    parts = []
                    if fxv is not None and abs(float(fxv)) > 0:
                        parts.append(f"fx:{float(fxv):+.2f}")
                    if pv is not None and abs(float(pv)) > 0:
                        parts.append(f"rates:{float(pv):+.2f}")
                    if rv is not None and abs(float(rv)) > 0:
                        parts.append(f"risk:{float(rv):+.2f}")
                    if parts:
                        news_lines.append("news: " + title + " (" + ", ".join(parts) + ")")
                    else:
                        news_lines.append("news: " + title)
        except Exception:
            news_lines = []

        if news_lines:
            for x in news_lines:
                x2 = _clean_label(x)
                if x2 and x2 not in reasons_all:
                    reasons_all.append(x2)

        out_rows[sector_display] = {
            "sector_display": sector_display,
            "policy_score": final_score_val,
            "flags": flags_all,
            "meta": {
                "why": seed_why or "auto+seed",
                "seed_score": seed_score,
                "delta_market": float(delta_market_val),
                "delta_news": float(delta_news_val),
                "delta": float(delta_market_val + float(delta_news_val)),
                "weights": w,
                "components": {
                    # hybrid側が期待する “compact” もここに置く（fx/risk/rates）
                    "fx": float(comps.get("fx", 0.0)),
                    "risk": float(comps.get("risk", 0.0)),
                    "rates": float(comps.get("jp_rates", 0.0)),
                },
                "components_full": comps,
                "component_detail": comp_detail_market,
                "news": {
                    "asof": news_asof,
                    "items_total": len(news_items) if isinstance(news_items, list) else 0,
                    "factors_sum": news_factors_sum if isinstance(news_factors_sum, dict) else {},
                    "sector_sum": {
                        # このセクターの値だけ入れる（全部入れると重い）
                        sector_display: float(news_sector_sum.get(sector_display_norm, 0.0) or 0.0)
                    },
                    "delta_news_detail": delta_news_detail,
                },
                "inputs": {
                    "usd_jpy_pct": inputs.get("usd_jpy_pct"),
                    "dxy_pct": inputs.get("dxy_pct"),
                    "tnx_pct": inputs.get("tnx_pct"),
                    "jgb10y_last": inputs.get("jgb10y_last"),
                    "jgb10y_source": inputs.get("jgb10y_source"),
                },
                "reasons": reasons_all,
            },
        }

    # seed に “33業種以外” があったら、落とさずに末尾で引き継ぐ（将来拡張用）
    for seed_key_raw, seed_row in seed_rows.items():
        if not isinstance(seed_row, dict):
            continue
        k_norm = _norm_key(seed_key_raw)
        if k_norm in weights_norm:
            continue
        out_rows[_norm_key(seed_key_raw) or str(seed_key_raw)] = {
            "sector_display": _norm_key(seed_key_raw) or str(seed_key_raw),
            "policy_score": _safe_float(seed_row.get("policy_score")) or 0.0,
            "flags": [
                _clean_label(x)
                for x in (seed_row.get("flags") if isinstance(seed_row.get("flags"), list) else [])
                if _clean_label(x)
            ],
            "meta": {
                "why": _clean_label((seed_row.get("meta") or {}).get("why")) if isinstance(seed_row.get("meta"), dict) else "seed_only",
                "note": "not_in_33_sectors_kept",
            },
        }

    # meta（全体ログ）
    meta: Dict[str, Any] = dict(seed_meta)
    meta["source"] = "fundamentals+policy_news+seed"
    meta["asof_source"] = "fundamentals/latest_fundamentals.json"
    meta["fundamentals_asof_date"] = asof
    meta["fund_meta_asof"] = (fund.get("meta") or {}).get("asof") if isinstance(fund.get("meta"), dict) else None

    meta["policy_news_source"] = "media/aiapp/policy_news/latest_policy_news.json"
    meta["policy_news_asof"] = news_asof
    meta["policy_news_items"] = len(news_items) if isinstance(news_items, list) else 0
    meta["policy_news_meta"] = news_meta if isinstance(news_meta, dict) else None
    meta["policy_news_factors_sum"] = news_factors_sum if isinstance(news_factors_sum, dict) else {}
    meta["policy_news_sector_sum_size"] = len(news_sector_sum)

    meta["coeffs"] = COEFS
    meta["market_inputs"] = {
        "usd_jpy_pct": inputs.get("usd_jpy_pct"),
        "dxy_pct": inputs.get("dxy_pct"),
        "tnx_pct": inputs.get("tnx_pct"),
        "jgb10y_last": inputs.get("jgb10y_last"),
        "n225_pct": inputs.get("n225_pct"),
        "nif_pct": inputs.get("nif_pct"),
        "fund_errors": inputs.get("fund_errors"),
        "fund_notes": inputs.get("fund_notes"),
    }

    # compact / full を両方置く（読む側の互換）
    meta["components"] = {
        "fx": float(comps.get("fx", 0.0)),
        "risk": float(comps.get("risk", 0.0)),
        "rates": float(comps.get("jp_rates", 0.0)),
    }
    meta["components_full"] = comps
    meta["warnings"] = warnings

    if delta_market_stats:
        meta["delta_market_stats"] = {
            "min": float(min(delta_market_stats)),
            "max": float(max(delta_market_stats)),
            "avg": float(sum(delta_market_stats) / max(1, len(delta_market_stats))),
        }
    if delta_news_stats:
        meta["delta_news_stats"] = {
            "min": float(min(delta_news_stats)),
            "max": float(max(delta_news_stats)),
            "avg": float(sum(delta_news_stats) / max(1, len(delta_news_stats))),
        }
    if delta_total_stats:
        meta["delta_total_stats"] = {
            "min": float(min(delta_total_stats)),
            "max": float(max(delta_total_stats)),
            "avg": float(sum(delta_total_stats) / max(1, len(delta_total_stats))),
        }

    # 日付ズレがあれば残す（落とさない）
    try:
        if isinstance(news_asof, str) and len(news_asof) >= 10:
            if news_asof[:10] != asof:
                meta["asof_mismatch"] = {"fund_asof": asof, "news_asof": news_asof[:10]}
    except Exception:
        pass

    return PolicySnapshot(
        asof=asof,
        sector_rows=out_rows,
        meta=meta,
    )


def emit_policy_json(snap: PolicySnapshot) -> None:
    POLICY_DIR.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "asof": snap.asof,
        "sector_rows": snap.sector_rows,
        "meta": snap.meta,
    }

    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # latest
    LATEST_POLICY.write_text(s, encoding="utf-8")

    # stamped
    stamped = POLICY_DIR / f"{_dt_now_stamp()}_policy.json"
    stamped.write_text(s, encoding="utf-8")