# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from hashlib import sha1
from typing import List, Dict, Tuple, Optional, Any

from django.conf import settings
from django.core.cache import cache

from ..models_advisor import AdviceSession, AdviceItem
# ★ セクター強弱（RS）テーブルを取得
from .market import latest_sector_strength  # 例: { "情報・通信": {"rs_score": 0.42, "date": "2025-01-01", ...}, ... }
from .sector_map import map_pf_sectors, normalize_sector

# =========================
# 表示用構造
# =========================
@dataclass
class AdviceItemView:
    id: int                 # 永続化前は0（DB保存時に実IDへ）
    message: str
    score: float            # 0..1（大きいほど上位）
    taken: bool = False     # 既定チェック

# =========================
# ユーティリティ
# =========================
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _sf(x, d=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d

def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()

def _cooldown_pass(msg: str, days: int = 7) -> bool:
    """同一メッセージの連投抑制（days 以内は出さない）"""
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True

# =========================
# policy.json（自動読み込み）
# =========================
_POLICY_CACHE_KEY = "advisor:policy:blob"
_POLICY_MTIME_KEY = "advisor:policy:mtime"
_DEFAULT_POLICY_REL = "media/advisor/policy.json"  # MEDIA_ROOT基準

def _policy_path() -> str:
    # MEDIA_ROOT/ media/advisor/policy.json（相対でも絶対でもOK）
    rel = getattr(settings, "ADVISOR_POLICY_PATH", _DEFAULT_POLICY_REL)
    if os.path.isabs(rel):
        return rel
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return os.path.join(base, rel)

def _load_policy_from_disk() -> Optional[dict]:
    path = _policy_path()
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _get_policy() -> Optional[dict]:
    """
    - ファイルの mtime を見てキャッシュ更新
    - 失敗時は None（補正なし）
    """
    path = _policy_path()
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else None
    except Exception:
        mtime = None

    cached_mtime = cache.get(_POLICY_MTIME_KEY)
    cached_policy = cache.get(_POLICY_CACHE_KEY)

    if mtime and (cached_policy is None or cached_mtime != mtime):
        policy = _load_policy_from_disk()
        cache.set(_POLICY_CACHE_KEY, policy, timeout=60 * 60)  # 1h
        cache.set(_POLICY_MTIME_KEY, mtime, timeout=60 * 60)
        return policy
    return cached_policy

def _rs_thresholds_from_policy() -> tuple[float, float]:
    """
    policy.json の rs_thresholds を読み取り、(weak, strong) を返す。
    無ければデフォルト (-0.25, 0.35)。
    """
    try:
        policy = _get_policy() or {}
        rs = policy.get("rs_thresholds") or {}
        weak = float(rs.get("weak", -0.25))
        strong = float(rs.get("strong", 0.35))
        # ありえない順序になっていたら補正
        if weak > strong:
            weak, strong = -0.25, 0.35
        return weak, strong
    except Exception:
        return (-0.25, 0.35)

def _notify_thresholds_from_policy() -> Dict[str, float]:
    """
    policy.json から通知しきい値群を取得。無ければデフォルトを返す。
    返り値キー:
      - liquidity_low:  流動性がこの%を下回れば警告（default 50）
      - margin_high:    信用比率がこの%を超えれば警告（default 60）
      - top_share_high: 上位セクター集中（%）（default 45）
      - uncat_share_high: 未分類セクター比率（%）（default 40）
      - pf_cash_low_for_bull: PF強気時に現金がこの%未満なら注意（default 30）
      - realized_month_min: 今月実現益がこの金額超で利確提案（default 0）
    """
    defaults = {
        "liquidity_low": 50.0,
        "margin_high": 60.0,
        "top_share_high": 45.0,
        "uncat_share_high": 40.0,
        "pf_cash_low_for_bull": 30.0,
        "realized_month_min": 0.0,
    }
    try:
        pol = _get_policy() or {}
        tbl = pol.get("notify_thresholds") or {}
        out = {}
        for k, v in defaults.items():
            try:
                out[k] = float(tbl.get(k, v))
            except Exception:
                out[k] = v
        return out
    except Exception:
        return defaults

def _notify_templates_from_policy() -> Dict[str, Dict[str, str]]:
    """
    policy.json の notify_templates を取得。無ければデフォルトを返す。
    キーはカテゴリ（LIQ/MARGIN/TOP_HEAVY/UNCAT/RS_WEAK/RS_STRONG/PF_RS_WEAK/PF_RS_STRONG/
                    BREADTH_WEAK/BREADTH_STRONG/REALIZED_GAIN/ROI_NEG）
    値は {"soft": "...", "medium": "...", "strong": "..."} の辞書。
    """
    default = {
        "LIQ": {
            "soft":   "流動性 {liq:.1f}% とやや低め。現金化余地の点検を。",
            "medium": "流動性 {liq:.1f}%。余力が薄いので、現金化余地の確保を検討。",
            "strong": "流動性 {liq:.1f}% と低水準。早めの圧縮や利確でキャッシュ確保を優先。"
        },
        "MARGIN": {
            "soft":   "信用比率 {mr:.1f}%。レバレッジの点検を。",
            "medium": "信用比率 {mr:.1f}%。下落耐性の再確認を推奨。",
            "strong": "信用比率 {mr:.1f}%。圧縮やヘッジを含めた迅速な対応を。"
        },
        "TOP_HEAVY": {
            "soft":   "セクター偏在（{sector} {share:.1f}%）。分散を検討。",
            "medium": "セクター偏在が目立つ（{sector} {share:.1f}%）。段階的なリバランスを。",
            "strong": "セクター偏在が過大（{sector} {share:.1f}%）。速やかな縮小と分散を。"
        },
        "UNCAT": {
            "soft":   "未分類セクター比率 {uncat:.1f}%。業種タグ整備を。",
            "medium": "未分類セクターが多め {uncat:.1f}%。早めにタグ整備し方針明確化を。",
            "strong": "未分類セクター比率が高い {uncat:.1f}%。管理性向上のため即整備を。"
        },
        "RS_WEAK": {
            "soft":   "主力セクター「{sec}」の相対強弱が弱め（{rs:+.2f}）。比率見直しを。",
            "medium": "主力セクター「{sec}」が弱気（{rs:+.2f}）。圧縮や損切りを検討。",
            "strong": "主力セクター「{sec}」が明確に弱気（{rs:+.2f}）。速やかな縮小/撤退を。"
        },
        "RS_STRONG": {
            "soft":   "主力セクター「{sec}」がやや強気（{rs:+.2f}）。利確計画を。",
            "medium": "主力セクター「{sec}」が強気（{rs:+.2f}）。利を伸ばしつつ段階利確を。",
            "strong": "主力セクター「{sec}」が力強い（{rs:+.2f}）。伸ばしつつ計画的に利益確定を。"
        },
        "PF_RS_WEAK": {
            "soft":   "PF相対強弱が弱め（{wrs:+.2f}）。守りを意識。",
            "medium": "PFが弱気（{wrs:+.2f}）。信用圧縮やヘッジを検討。",
            "strong": "PFが明確に弱気（{wrs:+.2f}）。信用縮小/ヘッジで下振れ耐性を強化。"
        },
        "PF_RS_STRONG": {
            "soft":   "PFがやや強気（{wrs:+.2f}）。現金{liq:.1f}%と薄めなら一部利確で補充を。",
            "medium": "PFが強気（{wrs:+.2f}）。現金{liq:.1f}%→利確で弾を補充し好機に備える。",
            "strong": "PFが強勢（{wrs:+.2f}）。現金{liq:.1f}%→過度の枯渇を避けるため利確で確保。"
        },
        "BREADTH_WEAK": {
            "soft":   "地合いやや弱（{regime}）。守り寄りの配分を意識。",
            "medium": "地合い弱（{regime}）。信用圧縮・現金比率引上げを優先。",
            "strong": "地合い悪化（{regime}）。迅速な縮小と防御を最優先。"
        },
        "BREADTH_STRONG": {
            "soft":   "地合いやや良好（{regime}）。トレンドに沿って慎重に回す。",
            "medium": "地合い良好（{regime}）。利確・乗り換えを計画的に。",
            "strong": "地合い強（{regime}）。計画的な増減で優位維持と利益確定を両立。"
        },
        "REALIZED_GAIN": {
            "soft":   "今月は実現益あり。含み益上位から一部利確の検討を。",
            "medium": "今月実現益。利益の再配分と段階利確を進める。",
            "strong": "今月実現益が積み上がり。利益確定ルールで確実に回収を。"
        },
        "ROI_NEG": {
            "soft":   "評価ROI {roi:.2f}%。損失限定ルールの点検を。",
            "medium": "評価ROI {roi:.2f}%。逆指値/縮小など損失限定を再設定。",
            "strong": "評価ROI {roi:.2f}%。速やかな縮小/撤退を含め対応を。"
        },
    }
    try:
        pol = _get_policy() or {}
        custom = pol.get("notify_templates") or {}
        # マージ（category→tone）
        merged = {}
        for cat, tones in default.items():
            ct = custom.get(cat) or {}
            merged[cat] = {
                "soft":   str(ct.get("soft", tones["soft"])),
                "medium": str(ct.get("medium", tones["medium"])),
                "strong": str(ct.get("strong", tones["strong"])),
            }
        return merged
    except Exception:
        return default


def _pick_tone(delta: float, soft_band: float = 0.10, strong_band: float = 0.30) -> str:
    """
    しきい値からの“離れ具合” delta を 0..∞ で受け取り、soft/medium/strong を返す。
    例: delta=0.05→soft, 0.2→medium, 0.4→strong
    """
    if delta >= strong_band:
        return "strong"
    if delta >= soft_band:
        return "medium"
    return "soft"

def _notify_thresholds() -> Dict[str, float]:
    """
    通知のしきい値を policy.json から読み込む（無ければデフォルト）。
    keys:
      gap_min(%) / liq_max(%) / margin_min(%) / top_share_max(%) / uncat_share_max(%)
      breadth_bad / breadth_good
    """
    defaults = {
        "gap_min": 20.0,          # ROI乖離がこれ以上で通知
        "liq_max": 50.0,          # 流動性がこれ未満で通知
        "margin_min": 60.0,       # 信用比率がこれ以上で通知
        "top_share_max": 45.0,    # 最大セクターの比率がこれ以上で通知
        "uncat_share_max": 40.0,  # 未分類セクターの比率がこれ以上で通知
        "breadth_bad": -0.35,     # 地合いスコアがこれ以下で悪化通知
        "breadth_good": 0.35,     # 地合いスコアがこれ以上で好転通知
    }
    try:
        pol = _get_policy() or {}
        nt = pol.get("notify_thresholds") or {}
        out = {**defaults}
        for k, v in nt.items():
            try:
                out[k] = float(v)
            except Exception:
                pass
        return out
    except Exception:
        return defaults


# =========================
# 地合い（ブレッドス）補助
# =========================
def _breadth_snapshot() -> Dict[str, Any]:
    """
    market.breadth_regime() を呼び出して
    {"score": -1..+1, "regime": "..."} を返す。失敗時は neutral。
    """
    try:
        from .market import breadth_regime
        br = breadth_regime()
        return br if isinstance(br, dict) else {"score": 0.0, "regime": "NEUTRAL"}
    except Exception:
        return {"score": 0.0, "regime": "NEUTRAL"}

# =========================
# RS（相対強弱）関連
# =========================
def _get_rs_table() -> Dict[str, Dict[str, Any]]:
    """
    latest_sector_strength() のラッパ。
    エラー時は空dict。
    """
    try:
        tbl = latest_sector_strength() or {}
        # 値の整形：最低限 rs_score は float に
        out: Dict[str, Dict[str, Any]] = {}
        for sec, row in tbl.items():
            try:
                rs = float((row or {}).get("rs_score", 0.0))
            except Exception:
                rs = 0.0
            out[sec] = dict(row or {})
            out[sec]["rs_score"] = rs
        return out
    except Exception:
        return {}

def _pf_weighted_rs(sectors: List[Dict[str, Any]], rs_table: Dict[str, Dict[str, Any]]) -> float:
    """
    ポート全体の加重RS（セクター正規化版）
    """
    total_mv = sum(max(0.0, _sf(s.get("mv"))) for s in (sectors or [])) or 1.0
    acc = 0.0
    for s in (sectors or []):
        sec_raw = (s.get("sector") or "").strip()
        sec = normalize_sector(sec_raw)  # ← ★正規化
        mv = max(0.0, _sf(s.get("mv")))
        rs = float((rs_table.get(sec) or {}).get("rs_score", 0.0))
        acc += rs * (mv / total_mv)
    return float(acc)

# =========================
# 特徴量抽出（将来ML用）
# =========================
# 既存互換（簡易特徴量）
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",
    "top_sector_ratio",
    "uncat_sector_ratio",
    "win_ratio",
]

def extract_features_for_learning(kpis: Dict[str, Any], sectors: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    学習用の数値特徴量（フルセット）
    - ストック：総資産/評価・清算価値/投下資金/現金/未実現/信用含み/信用比率/流動性/ROI各種/乖離
    - フロー：今月/累計の実現損益・配当
    - 行動：勝率
    - 構造：セクタ上位の騰落・構成比・未分類比率
    - 比率：現金比率、評価に対する現金・清算の比、ROI×流動性の合成指標 等
    - 需給：主力セクターのRS、PF加重RS
    """
    f: Dict[str, float] = {}

    # ---- ストック ----
    total_assets   = _sf(kpis.get("total_assets"))
    cash_total     = _sf(kpis.get("cash_total"))
    invested       = _sf(kpis.get("invested"))
    liquidation    = _sf(kpis.get("liquidation"))
    unrealized     = _sf(kpis.get("unrealized_pnl"))
    margin_unreal  = _sf(kpis.get("margin_unrealized"))
    liq_rate_pct   = _sf(kpis.get("liquidity_rate_pct"))
    margin_ratio   = _sf(kpis.get("margin_ratio_pct"))
    roi_eval       = kpis.get("roi_eval_pct")
    roi_liquid     = kpis.get("roi_liquid_pct")
    roi_gap_abs    = kpis.get("roi_gap_abs")

    f["total_assets"]   = total_assets
    f["cash_total"]     = cash_total
    f["invested"]       = invested
    f["liquidation"]    = liquidation
    f["unrealized_pnl"] = unrealized
    f["margin_unrealized"] = margin_unreal
    f["liquidity_rate_pct"] = liq_rate_pct
    f["margin_ratio_pct"]   = margin_ratio
    f["roi_eval_pct"]   = _sf(roi_eval, 0.0)
    f["roi_liquid_pct"] = _sf(roi_liquid, 0.0)
    f["roi_gap_abs"]    = _sf(roi_gap_abs, 0.0)

    # 派生
    f["cash_ratio_total"] = (cash_total / total_assets) if total_assets > 0 else 0.0
    f["liq_ratio_total"]  = (liquidation / total_assets) if total_assets > 0 else 0.0

    # ---- フロー（今月/累計）----
    realized_month = _sf(kpis.get("realized_month"))
    dividend_month = _sf(kpis.get("dividend_month"))
    realized_cum   = _sf(kpis.get("realized_cum"))
    dividend_cum   = _sf(kpis.get("dividend_cum"))

    f["realized_month"] = realized_month
    f["dividend_month"] = dividend_month
    f["realized_cum"]   = realized_cum
    f["dividend_cum"]   = dividend_cum

    # 正規化（総資産基準）
    denom = total_assets if total_assets > 0 else 1.0
    f["realized_month_ratio"] = realized_month / denom
    f["dividend_month_ratio"] = dividend_month / denom
    f["realized_cum_ratio"]   = realized_cum / denom
    f["dividend_cum_ratio"]   = dividend_cum / denom

    # ---- 行動 ----
    f["win_ratio"] = _sf(kpis.get("win_ratio"))

    # ---- 構造（セクタ上位）----
    top = sorted(sectors or [], key=lambda s: _sf(s.get("mv")), reverse=True)[:3]
    total_mv = sum(_sf(s.get("mv")) for s in (sectors or [])) or 1.0
    uncat_mv = 0.0
    for i, s in enumerate(top):
        f[f"sector{i+1}_mv"]    = _sf(s.get("mv"))
        f[f"sector{i+1}_rate"]  = _sf(s.get("rate"))
        share = _sf(s.get("mv")) / total_mv * 100.0
        f[f"sector{i+1}_share"] = share
    for s in (sectors or []):
        if s.get("sector") == "未分類":
            uncat_mv += _sf(s.get("mv"))
    f["uncat_sector_ratio"] = (uncat_mv / total_mv * 100.0) if total_mv > 0 else 0.0

    # ---- 需給（RS）：主力セクターRS & PF加重RS ----
    rs_table = _get_rs_table()
    if sectors and rs_table:
        top_sec = (sectors[0].get("sector") or "").strip()
        f["top_sector_rs"] = float((rs_table.get(top_sec) or {}).get("rs_score", 0.0))
        f["pf_weighted_rs"] = _pf_weighted_rs(sectors, rs_table)
    else:
        f["top_sector_rs"] = 0.0
        f["pf_weighted_rs"] = 0.0

    # ---- 合成/安全度 ----
    f["roi_times_liquidity"] = _sf(roi_eval, 0.0) * (liq_rate_pct / 100.0)
    f["safety_score_like"] = max(0.0, 1.0 - (margin_ratio / 100.0)) * (liq_rate_pct / 100.0)

    # NaN/inf 防御
    clean = {}
    for k, v in f.items():
        try:
            vv = float(v)
            if vv != vv:  # NaN
                vv = 0.0
        except Exception:
            vv = 0.0
        clean[k] = vv
    return clean

def _build_features(kpis: Dict, sectors: List[Dict]) -> Dict[str, float]:
    """
    互換の軽量版（旧FEATURES）を満たしつつ、上のフル特徴量を基に補完。
    """
    f = extract_features_for_learning(kpis, sectors)

    total_assets = max(1.0, _pct(kpis.get("total_assets")))
    realized_month_ratio = _pct(kpis.get("realized_month")) / total_assets

    # セクタ構成
    top_ratio = 0.0
    uncat_ratio = f.get("uncat_sector_ratio", 0.0) / 100.0  # 既に%で計算済み
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top_ratio = _pct(sectors[0].get("mv")) / total_mv if sectors else 0.0

    f.setdefault("roi_gap_abs", _pct(kpis.get("roi_gap_abs")))
    f.setdefault("liquidity_rate_pct", _pct(kpis.get("liquidity_rate_pct")))
    f.setdefault("margin_ratio_pct", _pct(kpis.get("margin_ratio_pct")))
    f.setdefault("realized_month_ratio", realized_month_ratio)
    f.setdefault("top_sector_ratio", top_ratio * 100.0)
    f.setdefault("uncat_sector_ratio", uncat_ratio * 100.0 if uncat_ratio <= 1 else float(f.get("uncat_sector_ratio", 0.0)))
    f.setdefault("win_ratio", _pct(kpis.get("win_ratio")))
    # NaN 無効化
    return {k: (0.0 if v != v else float(v)) for k, v in f.items()}

# =========================
# ルール（フォールバック提案）
# =========================
_CATEGORY_PATTERNS = [
    ("GAP",     ["乖離", "評価ROIと現金ROI"]),
    ("LIQ",     ["流動性", "現金化余地"]),
    ("MARGIN",  ["信用比率", "レバレッジ"]),
    ("SECTOR",  ["セクター偏在", "強弱"]),
    ("UNCAT",   ["未分類セクター", "業種タグ"]),
    ("REALIZE", ["実現益", "利確", "段階的利確"]),
    ("NEGROI",  ["評価ROIが", "損失限定"]),
]

def _category_of(msg: str) -> str:
    m = msg or ""
    for cat, keys in _CATEGORY_PATTERNS:
        if any(k in m for k in keys):
            return cat
    return "OTHER"

def _header_note(kpis: Dict, sectors: List[Dict]) -> str:
    ta = kpis.get("total_assets", 0)
    un = kpis.get("unrealized_pnl", 0)
    ca = kpis.get("cash_total", 0)
    re = kpis.get("roi_eval_pct", None)
    rl = kpis.get("roi_liquid_pct", None)

    parts = [
        f"総資産¥{ta:,}",
        f"含み損益{'+' if un >= 0 else ''}¥{un:,}",
        f"現金¥{ca:,}",
    ]
    if re is not None:
        parts.append(f"評価ROI{re:.2f}%")
    if rl is not None:
        parts.append(f"現金ROI{rl:.2f}%")

    # PF加重RS（あれば）
    rs_table = _get_rs_table()
    if rs_table and sectors:
        try:
            w_rs = _pf_weighted_rs(sectors, rs_table)
            parts.append(f"PF相対強弱{w_rs:+.2f}")
        except Exception:
            pass

    # 地合い（ブレッドス）も表示（あれば）
    try:
        br = _breadth_snapshot()  # {"score": -1..+1, "regime": "..."}
        parts.append(f"地合い{br.get('regime','NEUTRAL')}({float(br.get('score',0.0)):+.2f})")
    except Exception:
        pass

    return "、".join(parts) + "。"

def _rs_thresholds_by_env() -> Tuple[float, float]:
    """
    地合いスコア(breadth)から、RSの弱気・強気しきい値を動的決定。
    戻り値: (rs_weak_th, rs_strong_th)
    """
    try:
        br = _breadth_snapshot()
        score = float(br.get("score", 0.0))
    except Exception:
        score = 0.0

    # 通常は (-0.25, +0.35)
    weak, strong = -0.25, 0.35

    if score <= -0.3:
        # 弱地合い → 警戒モード
        weak, strong = -0.15, 0.25
    elif score >= 0.3:
        # 強地合い → 攻めモード
        weak, strong = -0.35, 0.45

    return weak, strong

def _rules(kpis: Dict, sectors: List[Dict]) -> List[AdviceItemView]:
    items: List[AdviceItemView] = []

    th = _notify_thresholds()                # ← ★ しきい値を一元管理
    thr_weak, thr_strong = _rs_thresholds_from_policy_or_env()  # ← RSは学習/環境適応

    # === ROI 乖離 ===
    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= th["gap_min"]:
        # 大きいほど強く出す
        score = min(1.0, max(0.35, (gap - th["gap_min"]) / 80.0 + 0.35))
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt（閾値{th['gap_min']:.1f}）。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItemView(0, msg, score))

    # === 流動性 ===
    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < th["liq_max"]:
        # 低いほど強く出す
        score = min(1.0, max(0.35, (th["liq_max"] - liq) / 30.0 + 0.35))
        msg = f"流動性 {liq:.1f}%（閾値{th['liq_max']:.1f}%）と低め。現金化余地の確保を検討。"
        items.append(AdviceItemView(0, msg, score))

    # === 信用比率 ===
    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= th["margin_min"]:
        score = min(1.0, max(0.35, (mr - th["margin_min"]) / 30.0 + 0.35))
        msg = f"信用比率が {mr:.1f}%（閾値{th['margin_min']:.1f}%）。レバレッジと下落耐性を再確認。"
        items.append(AdviceItemView(0, msg, score))

    # === セクター偏在／未分類（★正規化して判定） ===
    norm_sectors = map_pf_sectors(sectors) if sectors else []
    if norm_sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in norm_sectors) or 1.0
        top = norm_sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= th["top_share_max"]:
            score = min(1.0, max(0.35, (top_ratio - th["top_share_max"]) / 25.0 + 0.35))
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}% ≥ {th['top_share_max']:.1f}%）。分散を検討。"
            items.append(AdviceItemView(0, msg, score))

        uncat_mv = 0.0
        for s in norm_sectors:
            if normalize_sector(s.get("sector") or "") == "未分類":
                uncat_mv += _pct(s.get("mv"))
        if uncat_mv > 0:
            un_ratio = uncat_mv / total_mv * 100.0
            if un_ratio >= th["uncat_share_max"]:
                score = min(0.9, max(0.35, (un_ratio - th["uncat_share_max"]) / 30.0 + 0.35))
                msg = f"未分類セクター比率 {un_ratio:.1f}% ≥ {th['uncat_share_max']:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItemView(0, msg, score))

    # === 今月実現益 ===
    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = 0.5
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItemView(0, msg, score))

    # === ネガティブROI ===
    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItemView(0, msg, score))

    # === セクター強弱（RS） ===
    rs_table = _get_rs_table()
    try:
        if sectors and rs_table:
            top_sec = sectors[0].get("sector")
            if top_sec and top_sec in rs_table:
                rs = float(rs_table[top_sec].get("rs_score", 0.0))
                if rs <= thr_weak:
                    score = min(0.95, max(0.25, abs(rs)))
                    items.append(AdviceItemView(
                        0,
                        f"主力セクター「{top_sec}」の相対強弱が弱気（{rs:+.2f}≤{thr_weak:+.2f}）。比率圧縮や損切りを検討。",
                        score
                    ))
                elif rs >= thr_strong:
                    score = min(0.9, max(0.35, rs))
                    items.append(AdviceItemView(
                        0,
                        f"主力セクター「{top_sec}」の相対強弱が強気（{rs:+.2f}≥{thr_strong:+.2f}）。利を伸ばしつつ、段階利確を計画。",
                        score
                    ))

            # PF全体の加重RSを見て、信用や流動性への示唆
            w_rs = _pf_weighted_rs(sectors, rs_table)
            if w_rs < thr_weak and mr >= 30:
                items.append(AdviceItemView(
                    0, f"ポート全体の相対強弱が弱め（{w_rs:+.2f}≤{thr_weak:+.2f}）。信用縮小やヘッジで下振れ耐性を。", 0.8
                ))
            if w_rs > thr_strong and liq < 30:
                items.append(AdviceItemView(
                    0, f"PF強気（{w_rs:+.2f}≥{thr_strong:+.2f}）だが現金が薄い（{liq:.1f}%）。一部利確で弾を補充。", 0.7
                ))
    except Exception:
        pass

    # === ブレッドス（地合い） ===
    try:
        br = _breadth_snapshot()  # {"score": -1..+1, "regime": "..."}
        br_score = float(br.get("score", 0.0))
        regime = br.get("regime", "NEUTRAL")
        if br_score <= th["breadth_bad"]:
            msg = f"地合いが弱い（ブレッドス判定: {regime}、{br_score:+.2f}≤{th['breadth_bad']:+.2f}）。信用圧縮・現金比率引上げを優先。"
            items.append(AdviceItemView(0, msg, min(1.0, 0.6 + abs(br_score) * 0.6)))
        elif br_score >= th["breadth_good"]:
            msg = f"地合いが良好（ブレッドス判定: {regime}、{br_score:+.2f}≥{th['breadth_good']:+.2f}）。トレンドに沿って利確・乗り換えを計画。"
            items.append(AdviceItemView(0, msg, min(0.9, 0.5 + br_score * 0.5)))
    except Exception:
        pass

    return items

def _post_process(items: List[AdviceItemView]) -> List[AdviceItemView]:
    seen = set()
    uniq: List[AdviceItemView] = []
    for it in items:
        key = it.message.strip()
        if key in seen:
            continue
        seen.add(key)
        if _cooldown_pass(key):
            uniq.append(it)
    # 上位3件は既定チェックON（UIの初期状態）
    uniq.sort(key=lambda x: x.score, reverse=True)
    for i, it in enumerate(uniq[:3]):
        it.taken = True
    return uniq

# =========================
# policy.json によるスコア補正（将来用）
# =========================
def _bonus_from_policy(msg: str, policy: dict) -> float:
    """
    policy.json からメッセージ/カテゴリの改善度を見て 0..1 のボーナスへ。
    - avg_improve: -1..1 を 0..1 へ線形変換
    - confidence:  0..1 の重み
    返り値: 0..1（None相当は0）
    """
    if not policy:
        return 0.0

    # メッセージ/カテゴリ重み（今は未使用だが互換維持）
    m_tbl = (policy.get("message") or {})
    c_tbl = (policy.get("category") or {})
    cat = "OTHER"
    for k, v in _CATEGORY_PATTERNS:
        if k in msg:
            cat = k
            break

    m = m_tbl.get((msg or "").strip())
    if m:
        base = (float(m.get("avg_improve", 0.0)) + 1.0) / 2.0
        conf = float(m.get("confidence", 0.0))
        return max(0.0, min(1.0, base * conf))

    c = c_tbl.get(cat)
    if c:
        base = (float(c.get("avg_improve", 0.0)) + 1.0) / 2.0
        conf = float(c.get("confidence", 0.0))
        return max(0.0, min(1.0, base * conf))

    # kind_weight がある場合にざっくり補正
    kw = policy.get("kind_weight")
    if isinstance(kw, dict):
        try:
            avg = sum(float(v) for v in kw.values()) / max(len(kw), 1)
        except Exception:
            avg = 1.0
        w = float(kw.get(cat, avg)) / (avg or 1.0)
        return max(0.0, min(1.0, 0.5 * w))

    return 0.0

def _score_with_policy(feats: dict) -> Optional[float]:
    """
    policy.json に weights/bias があれば 線形→ロジスティックで 0..1 へ。
    無ければ None（呼び出し側で無視）。
    """
    policy = _get_policy()
    if not policy:
        return None

    weights = policy.get("weights")
    if not isinstance(weights, dict):
        return None

    bias = float(policy.get("bias", 0.0))
    score = bias
    for k, v in feats.items():
        if k in weights:
            try:
                score += float(weights[k]) * float(v)
            except Exception:
                pass
    # ロジスティック
    try:
        import math
        score = 1.0 / (1.0 + math.exp(-score))
    except Exception:
        score = 0.5
    return max(0.0, min(1.0, float(score)))

def _apply_policy(items: List[AdviceItemView], kpis: Dict, sectors: List[Dict]) -> List[AdviceItemView]:
    """
    base_score(0..1) をポリシーボーナス(0..1)と合成。
    合成式： score' = clip( 0.6*base + 0.4*bonus )
    """
    policy = _get_policy()
    if not policy:
        return _post_process(items)

    boosted: List[AdviceItemView] = []
    for it in items:
        base = float(it.score)
        bonus = _bonus_from_policy(it.message, policy)
        score = max(0.0, min(1.0, 0.6 * base + 0.4 * bonus))
        boosted.append(AdviceItemView(id=it.id, message=it.message, score=score, taken=it.taken))

    boosted.sort(key=lambda x: x.score, reverse=True)
    for i, it in enumerate(boosted[:3]):
        it.taken = True
    return boosted

def _rs_thresholds_from_policy_or_env() -> Tuple[float, float]:
    """
    policy.json に rs_thresholds が入っていればそれを優先し、
    無ければ現在の地合いから動的に算出して返す。
    戻り値: (rs_weak_th, rs_strong_th)
    """
    # 1) policy.json 優先
    try:
        pol = _get_policy() or {}
        th = pol.get("rs_thresholds")
        if isinstance(th, dict):
            w = float(th.get("weak"))
            s = float(th.get("strong"))
            # weak < strong の関係を最低限保証
            if w < s:
                return (w, s)
    except Exception:
        pass

    # 2) 環境から動的決定（ブレッドス score）
    try:
        br = _breadth_snapshot()
        score = float(br.get("score", 0.0))
    except Exception:
        score = 0.0

    # デフォルト
    weak, strong = -0.25, 0.35
    if score <= -0.3:
        # 弱地合い → 警戒モード（弱気を早く検出）
        weak, strong = -0.15, 0.25
    elif score >= 0.3:
        # 強地合い → 攻めモード（強気をやや引き上げ）
        weak, strong = -0.35, 0.45
    return weak, strong

# =========================
# 週次/次の一手
# =========================
def weekly_report(kpis: Dict, sectors: List[Dict]) -> str:
    head = _header_note(kpis, sectors)
    sect = ", ".join([
        f"{s.get('sector', '-')}"
        f" {s.get('rate', s.get('share_pct', 0))}%"
        for s in sectors[:5]
    ]) or "--"
    return f"{head} セクター概況: {sect}。"

def next_move(kpis: Dict, sectors: List[Dict]) -> str:
    items = _post_process(_rules(kpis, sectors))
    bullets = " / ".join([it.message for it in items[:3]]) or "様子見。"
    return f"次の一手: {bullets}"

# =========================
# エントリポイント
# =========================
def summarize(kpis: Dict, sectors: List[Dict], variant: str = "A") -> Tuple[str, List[Dict], str, str, str]:
    """
    variant:
      'A' -> ルールのみ（RS対応込み）
      'B' -> ルール + policy.json（weightsがあれば線形、無ければメッセージ/カテゴリ重み）
    """
    ai_note = _header_note(kpis, sectors)

    # まずルールで候補（RS含む）
    base_items = _rules(kpis, sectors)

    items: List[AdviceItemView]
    if variant == "B":
        feats = _build_features(kpis, sectors)
        pol_score = _score_with_policy(feats)
        if pol_score is not None:
            mixed = []
            for it in base_items:
                score = max(0.0, min(1.0, 0.6 * float(it.score) + 0.4 * float(pol_score)))
                mixed.append(AdviceItemView(id=it.id, message=it.message, score=score, taken=it.taken))
            items = _post_process(mixed)
        else:
            items = _apply_policy(base_items, kpis, sectors)
    else:
        items = _post_process(base_items)

    # --- 通知のしきい値（環境適応）を適用して notify フラグを付与 ---
    try:
        rs_table = _get_rs_table()
        env = _env_for_notify(sectors, rs_table)
        base_thr = _notify_base_from_policy(default=0.55)
        thr = _decide_notify_threshold(base_thr, env)
    except Exception:
        thr = 0.55  # フォールバック

    ai_items = []
    for it in items:
        d = asdict(it)
        d["notify"] = bool(it.score >= thr)
        ai_items.append(d)

    session_id = sha1(ai_note.encode("utf-8")).hexdigest()[:8]
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)
    return ai_note, ai_items, session_id, weekly, nextmove

# =========================
# セッション永続化（ビュー/コマンドから呼ぶ）
# =========================
def ensure_session_persisted(ai_note: str, ai_items: list, kpis: dict, variant: str = "A"):
    """
    セッション永続化: AdviceSession/AdviceItem を保存
    - context_json にも ab_variant を残す
    - AdviceSession.variant にも保存
    """
    ctx = dict(kpis or {})
    ctx.setdefault("ab_variant", variant)

    session = AdviceSession.objects.create(
        context_json=ctx,
        note=ai_note[:200],
        variant=variant,
    )
    for item in ai_items:
        AdviceItem.objects.create(
            session=session,
            kind=item.get("kind", "REBALANCE"),
            message=item.get("message", ""),
            score=item.get("score", 0.0),
            taken=item.get("taken", False),
            reasons=item.get("reasons", []),
        )
    return ai_items
    
    
def _env_for_notify(sectors: List[Dict], rs_table: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """
    通知しきい値チューニング用の環境特徴量を返す。
    - breadth_score: ブレッドス指標（-1..+1）
    - pf_rs: PF加重RS（-1..+1）
    """
    # PF加重RS
    pf_rs = 0.0
    try:
        if sectors and rs_table:
            pf_rs = _pf_weighted_rs(sectors, rs_table)
    except Exception:
        pf_rs = 0.0

    # ブレッドス
    try:
        br = _breadth_snapshot() or {}
        bscore = float(br.get("score", 0.0))
    except Exception:
        bscore = 0.0

    return {"breadth_score": bscore, "pf_rs": pf_rs}
    
    
def _decide_notify_threshold(base: float, env: Dict[str, float]) -> float:
    """
    base: policy['notify']['base_score']（デフォルト 0.55）
    env:  {"breadth_score":-1..+1, "pf_rs":-1..+1}
    調整ルール（軽めの係数で安全側に）:
      - 地合い弱い（<=-0.35）→ -0.05（通知を出しやすく）
      - 地合い強い（>=+0.35）→ +0.03（厳しめ）
      - PFが弱い（<=-0.25）→ -0.03、強い（>=+0.25）→ +0.02
    クリップ: 0.45..0.75
    """
    t = float(base)
    b = float(env.get("breadth_score", 0.0))
    r = float(env.get("pf_rs", 0.0))

    if b <= -0.35:
        t -= 0.05
    elif b >= 0.35:
        t += 0.03

    if r <= -0.25:
        t -= 0.03
    elif r >= 0.25:
        t += 0.02

    return max(0.45, min(0.75, t))
    
def _notify_base_from_policy(default: float = 0.55) -> float:
    """
    policy.json の notify.base_score を取得。無ければ default。
    """
    try:
        pol = _get_policy() or {}
        notify = pol.get("notify") or {}
        return float(notify.get("base_score", default))
    except Exception:
        return float(default)