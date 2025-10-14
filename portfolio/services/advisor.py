# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from hashlib import sha1
from math import exp

from django.core.cache import cache

# 学習ポリシー・セッション永続化
from ..models_advisor import AdvicePolicy, AdviceSession, AdviceItem


# ====== 表示用構造 ======
@dataclass
class AdviceItemView:
    id: int                 # 永続化前は0固定（管理コマンドで保存時に実IDへ）
    message: str
    score: float            # 優先度（大きいほど上位）
    taken: bool = False     # UIのチェック既定


# ====== ユーティリティ ======
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()


def _cooldown_pass(msg: str, days: int = 7) -> bool:
    """同一メッセージの連投抑制。days 以内に同じテキストを出したら抑制。"""
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True


# ====== 特徴量抽出 ======
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",   # realized_month / total_assets
    "top_sector_ratio",
    "uncat_sector_ratio",
    "win_ratio",
]


def _build_features(kpis: Dict, sectors: List[Dict]) -> Dict[str, float]:
    total_assets = max(1.0, _pct(kpis.get("total_assets")))
    realized_month_ratio = _pct(kpis.get("realized_month")) / total_assets

    top_ratio = 0.0
    uncat_ratio = 0.0
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top_ratio = _pct(sectors[0].get("mv")) / total_mv if sectors else 0.0
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            uncat_ratio = _pct(uncat.get("mv")) / total_mv

    feats = {
        "roi_gap_abs": _pct(kpis.get("roi_gap_abs")),
        "liquidity_rate_pct": _pct(kpis.get("liquidity_rate_pct")),
        "margin_ratio_pct": _pct(kpis.get("margin_ratio_pct")),
        "realized_month_ratio": realized_month_ratio,
        "top_sector_ratio": top_ratio * 100.0,
        "uncat_sector_ratio": uncat_ratio * 100.0,
        "win_ratio": _pct(kpis.get("win_ratio")),
    }
    # 欠損・異常をゼロ化（NaN対策）
    return {k: (0.0 if v != v else float(v)) for k, v in feats.items()}


# ====== ルールベース（フォールバック） ======
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
    return "、".join(parts) + "。"


def _rules(kpis: Dict, sectors: List[Dict]) -> List[AdviceItemView]:
    items: List[AdviceItemView] = []

    # 1) ROI 乖離（評価 vs 現金化）
    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= 20:
        score = min(1.0, gap / 80.0)  # 乖離80ptで満点
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItemView(0, msg, score))

    # 2) 流動性が低い
    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < 50:
        score = min(1.0, (50 - liq) / 30)
        msg = f"流動性 {liq:.1f}% と低め。現金化余地の確保を検討。"
        items.append(AdviceItemView(0, msg, score))

    # 3) 信用比率が高すぎる
    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= 60:
        score = min(1.0, (mr - 60) / 30)
        msg = f"信用比率が {mr:.1f}%。レバレッジと下落耐性を再確認。"
        items.append(AdviceItemView(0, msg, score))

    # 4) セクター偏在
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top = sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= 45:
            score = min(1.0, (top_ratio - 45) / 25)
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}%）。分散を検討。"
            items.append(AdviceItemView(0, msg, score))
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            un_ratio = _pct(uncat.get("mv")) / total_mv * 100.0
            if un_ratio >= 40:
                score = min(0.8, (un_ratio - 40) / 30)
                msg = f"未分類セクター比率 {un_ratio:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItemView(0, msg, score))

    # 5) 今月の実現がプラス
    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = 0.5
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItemView(0, msg, score))

    # 6) 評価ROIがマイナス（守り）
    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItemView(0, msg, score))

    return items


# 変更：ユーザー選択を優先。未出の文だけ上位3件を既定ON
def _post_process(items: List[AdviceItemView], taken_map: Optional[Dict[str, bool]] = None) -> List[AdviceItemView]:
    taken_map = taken_map or {}

    # 重複・クールダウン
    seen = set()
    uniq: List[AdviceItemView] = []
    for it in items:
        key = it.message.strip()
        if key in seen:
            continue
        seen.add(key)
        if _cooldown_pass(key):
            uniq.append(it)

    # ポリシー/ルールのスコア降順
    uniq.sort(key=lambda x: x.score, reverse=True)

    # ここがポイント：ユーザーの最終選択を反映
    # - 既に履歴があればそれを使う
    # - 履歴がなければ“初回だけ”上位3件をON
    for i, it in enumerate(uniq):
        msg = it.message.strip()
        if msg in taken_map:
            it.taken = bool(taken_map[msg])
        else:
            it.taken = (i < 3)  # 未出メッセージのみ既定ON
    return uniq


# ====== モデル推論（任意） ======
def _sigmoid(z: float) -> float:
    try:
        return 1.0 / (1.0 + exp(-z))
    except OverflowError:
        return 0.0 if z < 0 else 1.0


def _score_with_policy(features: Dict[str, float]) -> Optional[float]:
    """有効なポリシーがあればスコア（0..1）を返す。無ければ None。"""
    policy: Optional[AdvicePolicy] = AdvicePolicy.objects.filter(enabled=True).order_by("-updated_at").first()
    if not policy:
        return None

    kind = policy.kind
    p = policy.params or {}
    x = features.copy()

    # 標準化（任意）
    norm = p.get("norm") or {}
    for k, s in norm.items():
        mu = float(s.get("mu", 0.0))
        sig = float(s.get("sigma", 1.0)) or 1.0
        if k in x:
            x[k] = (x[k] - mu) / sig

    if kind in (AdvicePolicy.Kind.LINEAR, AdvicePolicy.Kind.LOGREG):
        coef: Dict[str, float] = p.get("coef", {})
        bias = float(p.get("bias", 0.0))
        z = bias + sum(float(coef.get(k, 0.0)) * float(x.get(k, 0.0)) for k in FEATURES)
        return _sigmoid(z) if kind == AdvicePolicy.Kind.LOGREG else max(0.0, min(1.0, z))

    if kind == AdvicePolicy.Kind.SKLEARN and policy.model_blob:
        try:
            import joblib, io
            model = joblib.load(io.BytesIO(policy.model_blob))
            vec = [[float(x.get(k, 0.0)) for k in FEATURES]]
            prob = getattr(model, "predict_proba", None)
            if prob:
                return float(prob(vec)[0][1])
            pred = getattr(model, "predict", None)
            if pred:
                y = float(pred(vec)[0])
                return 0.9 if y >= 0.5 else 0.1
        except Exception:
            return None

    return None


# ====== 週次/次の一手 素案 ======
def weekly_report(kpis: Dict, sectors: List[Dict]) -> str:
    head = _header_note(kpis, sectors)
    sect = ", ".join([f"{s['sector']} {s['rate']}%" for s in sectors[:5]]) or "—"
    return f"{head} セクター概況: {sect}。勝率データは今後追加予定。"


def next_move(kpis: Dict, sectors: List[Dict]) -> str:
    items = _post_process(_rules(kpis, sectors))
    bullets = " / ".join([it.message for it in items[:3]]) or "様子見。"
    return f"次の一手: {bullets}"


# ====== kind 自動推定（保存用） ======
def _infer_kind(message: str) -> str:
    msg = message or ""
    if ("乖離" in msg) or ("評価ROIと現金ROI" in msg):
        return AdviceItem.Kind.REBALANCE  # ROI差→整理/リバランス扱い
    if ("流動性" in msg) or ("現金化余地" in msg):
        return AdviceItem.Kind.ADD_CASH
    if ("信用比率" in msg) or ("レバレッジ" in msg):
        return AdviceItem.Kind.REDUCE_MARGIN
    if ("セクター偏在" in msg):
        return AdviceItem.Kind.REBALANCE
    if ("未分類セクター" in msg) or ("業種タグ" in msg):
        return AdviceItem.Kind.REBALANCE
    if ("実現益" in msg) or ("利確" in msg):
        return AdviceItem.Kind.TRIM_WINNERS
    if ("評価ROIが" in msg) or ("損失限定" in msg):
        return AdviceItem.Kind.CUT_LOSERS
    return AdviceItem.Kind.REBALANCE


# ====== エントリポイント ======
def summarize(kpis: Dict, sectors: List[Dict]) -> Tuple[str, List[Dict], str, str, str]:
    ai_note = _header_note(kpis, sectors)

    base_items = _rules(kpis, sectors)
    feats = _build_features(kpis, sectors)

    # ポリシースコアのブレンド
    scored: List[AdviceItemView] = []
    policy_ok = False
    pol_score = _score_with_policy(feats)
    for it in base_items:
        score = it.score
        if pol_score is not None:
            score = max(0.0, min(1.0, 0.6 * float(it.score) + 0.4 * float(pol_score)))
            policy_ok = True
        scored.append(AdviceItemView(id=it.id, message=it.message, score=score, taken=it.taken))

    # ★ 履歴を読み込んで taken を反映
    taken_map = _load_taken_map()
    items = _post_process(scored if policy_ok else base_items, taken_map=taken_map)

    ai_items = [asdict(it) for it in items]
    session_id = _hash_msg(ai_note)[:8]
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)
    return ai_note, ai_items, session_id, weekly, nextmove


# 公開：外から特徴量を取りたい場合
def extract_features_for_learning(kpis: Dict, sectors: List[Dict]) -> Dict[str, float]:
    return _build_features(kpis, sectors)


def ensure_session_persisted(ai_note: str, ai_items: list, kpis: dict) -> list:
    """
    AI提案をDBに保存して永続化する。
    - 最新セッションを AdviceSession として保存
    - items を AdviceItem として紐付け
    """
    session = AdviceSession.objects.create(
        context_json=kpis,
        note=ai_note[:200]
    )

    for item in ai_items:
        msg = item.get("message", "") or ""
        AdviceItem.objects.create(
            session=session,
            kind=_infer_kind(msg),
            message=msg,
            score=float(item.get("score", 0.0)),
            taken=bool(item.get("taken", False)),
            reasons=item.get("reasons", []),
        )

    return ai_items
    
# 追加：直近の提案履歴から「その文面の taken 最終値」を辞書で取り出す
def _load_taken_map(days: int = 120) -> Dict[str, bool]:
    """
    同一 message（テキスト完全一致）単位で直近の taken を採用する。
    - 直近120日をデフォルト（必要なら調整）
    """
    from django.utils import timezone
    since = timezone.now() - timezone.timedelta(days=days)
    qs = AdviceItem.objects.filter(created_at__gte=since).order_by("message", "-created_at")

    taken_map: Dict[str, bool] = {}
    seen = set()
    for it in qs:
        msg = (it.message or "").strip()
        if msg in seen:
            continue
        seen.add(msg)
        taken_map[msg] = bool(it.taken)
    return taken_map