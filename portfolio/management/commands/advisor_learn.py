# portfolio/management/commands/advisor_learn.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import json
import glob
import tempfile
from pathlib import Path
from datetime import timedelta, date
from typing import Dict, List, Tuple

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum

from ...models_advisor import AdviceItem
from ...models import Holding, RealizedTrade
from ...models_cash import BrokerAccount, CashLedger


# ===================== ユーティリティ =====================
def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _atomic_write(fp: Path, data: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(fp.parent)) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.replace(tmp_path, fp)

def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ===================== 現在ポートフォリオのスナップショット =====================
def _holdings_snapshot() -> Dict[str, float]:
    """
    last_price 優先（なければ avg_cost）。評価額や比率など “今” をざっくり取得。
    """
    holdings = list(Holding.objects.all())

    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0

    # セクター構成
    sector_map: Dict[str, Dict[str, float]] = {}

    for h in holdings:
        qty = _safe_float(getattr(h, "quantity", 0))
        unit = _safe_float(getattr(h, "avg_cost", 0))
        price = _safe_float(getattr(h, "last_price", None)) or unit
        mv = price * qty
        cost = unit * qty

        acc = (getattr(h, "account", "") or "").upper()
        sector = (getattr(h, "sector", None) or "").strip() or "未分類"

        if acc == "MARGIN":
            margin_mv += mv
            margin_cost += cost
        else:
            spot_mv += mv
            spot_cost += cost

        s = sector_map.setdefault(sector, {"mv": 0.0, "cost": 0.0})
        s["mv"] += mv
        s["cost"] += cost

    # 現金合計
    # opening_balance + ledgers の合計（JPY想定）
    accounts = list(BrokerAccount.objects.all().prefetch_related("ledgers"))
    cash_total = 0
    for a in accounts:
        led_sum = a.ledgers.aggregate(total=Sum("amount")).get("total") or 0
        bal = int(a.opening_balance or 0) + int(led_sum)
        if (a.currency or "JPY") == "JPY":
            cash_total += int(bal)

    total_eval_assets = spot_mv + margin_mv + cash_total
    gross_pos = max(spot_mv + margin_mv, 1.0)

    # 流動性（今すぐ現金化）
    margin_unrealized = margin_mv - margin_cost
    liquidation = spot_mv + margin_unrealized + cash_total
    liquidity_rate_pct = (liquidation / total_eval_assets * 100.0) if total_eval_assets > 0 else 0.0
    margin_ratio_pct = (margin_mv / gross_pos * 100.0) if gross_pos > 0 else 0.0

    # セクター上位の集中度
    sectors = sorted(
        [{"sector": k, "mv": v["mv"]} for k, v in sector_map.items()],
        key=lambda x: x["mv"],
        reverse=True,
    )
    top_share_pct = 0.0
    if sectors and gross_pos > 0:
        top_share_pct = sectors[0]["mv"] / gross_pos * 100.0

    return dict(
        spot_mv=spot_mv,
        margin_mv=margin_mv,
        cash_total=cash_total,
        liquidity_rate_pct=round(liquidity_rate_pct, 2),
        margin_ratio_pct=round(margin_ratio_pct, 2),
        top_sector_share_pct=round(top_share_pct, 2),
        total_eval_assets=total_eval_assets,
    )


# ===================== 履歴（実損・配当）の簡易指標 =====================
def _realized_signals(since: timezone.datetime) -> Dict[str, float]:
    """
    直近期間の実現損益・勝率・配当の合計などを返す。
    """
    trades = list(RealizedTrade.objects.filter(created_at__gte=since))
    win = sum(1 for r in trades if _safe_float(getattr(r, "pnl", 0)) > 0)
    lose = sum(1 for r in trades if _safe_float(getattr(r, "pnl", 0)) < 0)
    n = win + lose
    win_rate = (win / n) if n > 0 else 0.0
    realized_sum = float(sum(_safe_float(getattr(r, "pnl", 0)) for r in trades))

    # 配当（CashLedger の SourceType.DIVIDEND）
    div_qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.DIVIDEND,
        at__gte=since,
    )
    dividend_sum = float(sum(_safe_float(x.amount) for x in div_qs))

    return dict(
        win_rate=round(win_rate, 4),
        realized_sum=realized_sum,
        dividend_sum=dividend_sum,
        sample_n=n,
    )


# ===================== policy 重み計算（拡張版） =====================
def _base_kind_weights(since: timezone.datetime) -> Dict[str, float]:
    """
    AdviceItem の採用率から kind 重みの“素点”を作る（平均1.0に正規化）。
    ラプラス平滑化 (taken+1)/(n+2) を使用。
    """
    qs = AdviceItem.objects.filter(created_at__gte=since).values("kind", "taken")

    kinds: Dict[str, Dict[str, int]] = {}
    for row in qs:
        k = row["kind"] or "REBALANCE"
        acc = kinds.setdefault(k, {"n": 0, "taken": 0})
        acc["n"] += 1
        acc["taken"] += 1 if row["taken"] else 0

    if not kinds:
        # 実績がない場合のデフォルト
        raw = {
            "REBALANCE": 1.0,
            "ADD_CASH": 1.0,
            "TRIM_WINNERS": 1.0,
            "CUT_LOSERS": 1.0,
            "REDUCE_MARGIN": 1.0,
        }
    else:
        raw = {}
        for k, v in kinds.items():
            n = v["n"]
            t = v["taken"]
            p = (t + 1) / (n + 2) if n >= 0 else 0.5
            raw[k] = p

    avg = sum(raw.values()) / max(len(raw), 1)
    return {k: (v / (avg or 1.0)) for k, v in raw.items()}  # 平均1.0に正規化


def _apply_portfolio_signals(
    w: Dict[str, float],
    snap: Dict[str, float],
    sig: Dict[str, float],
) -> Dict[str, float]:
    """
    “現在の状態（レバ・流動性・集中度）” と “直近の実績（勝率/実損/配当）” を
    ヒューリスティックに重みに反映させる。
    すべて軽めの係数（±数％）で安全側に調整。
    """
    out = {**w}

    def mul(key: str, factor: float):
        out[key] = out.get(key, 1.0) * factor

    # ---- 現在の状態からの補正 ----
    margin = _safe_float(snap.get("margin_ratio_pct"))
    liquid = _safe_float(snap.get("liquidity_rate_pct"))
    topsec = _safe_float(snap.get("top_sector_share_pct"))

    # 信用比率が高い → レバ圧縮（REDUCE_MARGIN）・利益確定（TRIM_WINNERS）を強める
    if margin >= 60:
        mul("REDUCE_MARGIN", 1.15)
        mul("TRIM_WINNERS", 1.05)
    elif margin >= 40:
        mul("REDUCE_MARGIN", 1.08)

    # 流動性が低い → 現金確保（ADD_CASH, TRIM_WINNERS）を強める
    if liquid < 50:
        mul("ADD_CASH", 1.10)
        mul("TRIM_WINNERS", 1.05)

    # セクター集中が高い → リバランスを強める
    if topsec >= 40:
        mul("REBALANCE", 1.10)

    # ---- 直近の成果からの補正 ----
    win_rate = _safe_float(sig.get("win_rate"))
    realized_sum = _safe_float(sig.get("realized_sum"))
    dividend_sum = _safe_float(sig.get("dividend_sum"))

    # 勝率が低ければ CUT_LOSERS を強化 / 高ければ TRIM_WINNERS を強化
    if win_rate > 0.55:
        mul("TRIM_WINNERS", 1.05)
    elif win_rate < 0.45:
        mul("CUT_LOSERS", 1.06)

    # 実現損が大きくマイナス → リスク圧縮寄り
    if realized_sum < 0:
        mul("CUT_LOSERS", 1.05)
        mul("REDUCE_MARGIN", 1.05)

    # 配当が乗っている → キャッシュ積み増しや定期リバランスを少し強化
    if dividend_sum > 0:
        mul("ADD_CASH", 1.02)
        mul("REBALANCE", 1.02)

    return out


def _finalize_weights(
    weights: Dict[str, float],
    bias: float,
    clip_low: float,
    clip_high: float,
) -> Dict[str, float]:
    out = {}
    for k, v in weights.items():
        vv = max(clip_low, min(clip_high, bias * v))
        out[k] = round(vv, 4)
    return out


# ===================== 環境適応 RS しきい値（学習フェーズ統合） =====================
def _compute_env_adaptive_rs_thresholds() -> dict:
    """
    “今の地合い（breadth_regime）”から RS の弱/強しきい値を自動決定。
    将来は過去データ最適化に差し替え可。
    戻り値: {"weak": float, "strong": float, "source": "breadth", "breadth_score": float, "regime": str}
    """
    # デフォルト
    weak, strong = -0.25, 0.35
    score, regime = 0.0, "NEUTRAL"

    try:
        # services.market.breadth_regime を動的 import（依存トラブル回避）
        from ...services.market import breadth_regime  # type: ignore
        br = breadth_regime() or {}
        score = float(br.get("score", 0.0))
        regime = str(br.get("regime", "NEUTRAL"))
    except Exception:
        pass

    if score <= -0.3:
        # 弱地合い → 警戒（弱気を早めに検出）
        weak, strong = -0.15, 0.25
    elif score >= 0.3:
        # 強地合い → 攻め（強気のハードルをやや上げる）
        weak, strong = -0.35, 0.45

    return {
        "weak": float(weak),
        "strong": float(strong),
        "source": "breadth",
        "breadth_score": round(float(score), 3),
        "regime": regime,
    }


def _inject_rs_thresholds_into_policy(policy: dict) -> dict:
    """
    既存の policy dict に rs_thresholds を追加/上書きして返す。
    """
    policy = dict(policy or {})
    policy["rs_thresholds"] = _compute_env_adaptive_rs_thresholds()
    return policy


# ===================== メインコマンド =====================
class Command(BaseCommand):
    help = "過去のアドバイス採用実績 + ポートフォリオ状態から policy.json（重みファイル）を自動生成（拡張版）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="学習に使う日数（過去N日） default=90")
        parser.add_argument("--out", type=str, default="media/advisor/policy.json",
                            help="出力先パス（プロジェクトルートからの相対 or 絶対パス）")
        parser.add_argument("--bias", type=float, default=1.0, help="全体バイアス（倍率） default=1.0")
        parser.add_argument("--clip_low", type=float, default=0.80, help="重みの下限 default=0.80")
        parser.add_argument("--clip_high", type=float, default=1.30, help="重みの上限 default=1.30")
        parser.add_argument("--write-history", action="store_true",
                            help="media/advisor/history/policy_YYYY-MM-DD.json にもスナップショット保存")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        bias = float(opts["bias"])
        clip_low = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])

        # 出力パス解決
        out_path = Path(opts["out"])
        if not out_path.is_absolute():
            base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
            out_path = Path(base) / out_path

        since = timezone.now() - timedelta(days=days)

        # 1) AdviceItem から“素点”を作る
        base_w = _base_kind_weights(since)

        # 2) “現在の状態” & “直近実績” を取り込んで補正
        snap = _holdings_snapshot()
        sig = _realized_signals(since)
        tuned = _apply_portfolio_signals(base_w, snap, sig)

        # 3) クリップ & バイアス
        kind_weight = _finalize_weights(tuned, bias=bias, clip_low=clip_low, clip_high=clip_high)

        # 4) policy.json 生成（★ rs_thresholds を統合）
        payload: Dict = {
            "version": 2,  # ← 拡張版
            "updated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_days": days,
            "bias": bias,
            "clip": {"low": clip_low, "high": clip_high},
            "kind_weight": kind_weight,
            # 環境適応しきい値（推論側は policy.rs_thresholds を最優先利用）
            "rs_thresholds": _compute_env_adaptive_rs_thresholds(),
            # デバッグ用に信号も保存（軽量）
            "signals": {
                "snapshot": snap,
                "realized": sig,
                "base_weights": base_w,
            },
        }

        _atomic_write(out_path, json.dumps(payload, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.SUCCESS(
            f"Wrote policy.json → {out_path}  (kinds={len(kind_weight)})"
        ))

        # 5) 履歴にスナップショット（任意）
        if bool(opts.get("write-history")):
            # MEDIA_ROOT/ advisor/history/policy_YYYY-MM-DD.json
            base = Path(getattr(settings, "MEDIA_ROOT", "") or os.getcwd())
            hist_dir = base / "advisor" / "history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            hist_file = hist_dir / f"policy_{_today_str()}.json"
            _atomic_write(hist_file, json.dumps(payload, ensure_ascii=False, indent=2))
            self.stdout.write(self.style.SUCCESS(f"Saved history → {hist_file}"))

        # 6) 注意喚起（使っているデータの説明）
        self.stdout.write(
            self.style.NOTICE(
                "Signals used: AdviceItem採用率 + 現在の信用/流動性/集中度 + 直近勝率/実損/配当 + BreadthによるRSしきい値（環境適応）"
            )
        )