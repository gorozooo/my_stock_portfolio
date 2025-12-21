# aiapp/views/settings.py
from __future__ import annotations

import os
from typing import Any, Dict

import yaml
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from portfolio.models import UserSetting
from . import settings as _  # noqa: F401, keep
from aiapp.services.broker_summary import compute_broker_summaries
# 将来ほかの場所で使うかもしれないので import 自体は残しておく
from aiapp.services.policy_loader import load_short_aggressive_policy  # noqa: F401


# ----------------------------------------------------------------------
# ポリシーファイルへのパス & 読み書き共通ヘルパ
# ----------------------------------------------------------------------
def _policy_file_path() -> str:
    """
    short_aggressive.yml のフルパスを、ファイル構成から逆算して求める。

    views/settings.py  … aiapp/views
    aiapp_dir          … aiapp/
    policy             … aiapp/policies/short_aggressive.yml
    """
    here = os.path.abspath(os.path.dirname(__file__))  # aiapp/views
    aiapp_dir = os.path.dirname(here)                  # aiapp
    return os.path.join(aiapp_dir, "policies", "short_aggressive.yml")


def _load_policy_dict() -> Dict[str, Any]:
    """
    short_aggressive.yml を毎回読み直して dict で返す。
    （キャッシュは持たない。常に最新ファイルを見る）
    """
    path = _policy_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def _write_policy_dict(data: Dict[str, Any]) -> None:
    """
    dict をそのまま short_aggressive.yml に書き戻す。
    """
    path = _policy_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ----------------------------------------------------------------------
# UI 表示用のポリシーコンテキスト
# ----------------------------------------------------------------------
def _build_policy_context() -> Dict[str, Any]:
    """
    short_aggressive.yml の中身を UI 用に薄く整形して返す。
    読み込みも _load_policy_dict() で YAML を毎回読み直す。
    """
    data = _load_policy_dict()

    filters = data.get("filters") or {}
    fees = data.get("fees") or {}

    learn = data.get("learn") or {}
    limits = data.get("limits") or {}

    # 学習モードは 2択固定（collect / strict）
    learn_mode = str(learn.get("mode") or "collect").strip().lower()
    if learn_mode not in ("collect", "strict"):
        learn_mode = "collect"

    def _as_int(v, default: int) -> int:
        try:
            if v is None:
                return default
            return int(float(v))
        except Exception:
            return default

    def _as_float(v, default: float) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    return {
        # ---- core ----
        "mode": data.get("mode") or "",
        "risk_pct": data.get("risk_pct"),
        "credit_usage_pct": data.get("credit_usage_pct"),

        # ---- filters/fees ----
        "min_net_profit_yen": filters.get("min_net_profit_yen"),
        "min_reward_risk": filters.get("min_reward_risk"),
        "allow_negative_pl": filters.get("allow_negative_pl"),
        "commission_rate": fees.get("commission_rate"),
        "min_commission": fees.get("min_commission"),
        "slippage_rate": fees.get("slippage_rate"),

        # ---- PRO学習/運用（PROタブ）----
        "learn_mode": learn_mode,
        "max_positions": _as_int(limits.get("max_positions"), 5),
        "max_notional_per_trade_yen": _as_int(limits.get("max_notional_per_trade_yen"), 2_000_000),
        "min_notional_per_trade_yen": _as_int(limits.get("min_notional_per_trade_yen"), 0),
        "max_total_notional_yen": _as_int(limits.get("max_total_notional_yen"), 5_000_000),
        "horizon_bd": _as_int(limits.get("horizon_bd"), 3),
        "reserve_cash_yen": _as_int(limits.get("reserve_cash_yen"), 0),
        "strict_min_rr": _as_float(limits.get("strict_min_rr"), 1.0),
        "strict_min_net_profit_yen": _as_int(limits.get("strict_min_net_profit_yen"), 0),
    }


def _save_policy_basic_params(risk_pct: float, credit_usage_pct: float) -> None:
    """
    ポリシーファイル short_aggressive.yml の
    risk_pct / credit_usage_pct だけを上書き保存する。
    他の filters / fees / learn / limits は維持。
    """
    data = _load_policy_dict()
    data["risk_pct"] = float(risk_pct)
    data["credit_usage_pct"] = float(credit_usage_pct)
    _write_policy_dict(data)


def _save_policy_learning_params(
    *,
    learn_mode: str,
    max_positions: int,
    max_notional_per_trade_yen: int,
    min_notional_per_trade_yen: int,
    max_total_notional_yen: int,
    horizon_bd: int,
    reserve_cash_yen: int,
    strict_min_rr: float,
    strict_min_net_profit_yen: int,
) -> None:
    """
    PRO学習/運用の「建玉・資金ルール」を short_aggressive.yml に保存する。
    - learn.mode: collect / strict（2択固定）
    - limits.*: 建玉数・資金上限・口座枠・最大保有
    """
    mode = str(learn_mode or "collect").strip().lower()
    if mode not in ("collect", "strict"):
        mode = "collect"

    def clamp_int(v: int, lo: int, hi: int) -> int:
        try:
            v = int(v)
        except Exception:
            v = lo
        return max(lo, min(hi, v))

    def clamp_float(v: float, lo: float, hi: float) -> float:
        try:
            v = float(v)
        except Exception:
            v = lo
        return max(lo, min(hi, v))

    data = _load_policy_dict()

    data["learn"] = data.get("learn") or {}
    if not isinstance(data["learn"], dict):
        data["learn"] = {}
    data["learn"]["mode"] = mode

    data["limits"] = data.get("limits") or {}
    if not isinstance(data["limits"], dict):
        data["limits"] = {}

    data["limits"]["max_positions"] = clamp_int(max_positions, 1, 30)
    data["limits"]["max_notional_per_trade_yen"] = clamp_int(max_notional_per_trade_yen, 0, 50_000_000)
    data["limits"]["min_notional_per_trade_yen"] = clamp_int(min_notional_per_trade_yen, 0, 50_000_000)
    data["limits"]["max_total_notional_yen"] = clamp_int(max_total_notional_yen, 0, 500_000_000)
    data["limits"]["horizon_bd"] = clamp_int(horizon_bd, 1, 30)
    data["limits"]["reserve_cash_yen"] = clamp_int(reserve_cash_yen, 0, 500_000_000)

    data["limits"]["strict_min_rr"] = clamp_float(strict_min_rr, 0.0, 10.0)
    data["limits"]["strict_min_net_profit_yen"] = clamp_int(strict_min_net_profit_yen, 0, 50_000_000)

    _write_policy_dict(data)


def _save_policy_advanced_params(
    *,
    min_net_profit_yen: float | None,
    min_reward_risk: float | None,
    allow_negative_pl: bool | None,
    commission_rate: float | None,
    min_commission: float | None,
    slippage_rate: float | None,
) -> None:
    """
    filters / fees 系のしきい値を更新する。
    None の項目は「現状維持」。
    """
    data = _load_policy_dict()
    filters = data.get("filters") or {}
    fees = data.get("fees") or {}

    if min_net_profit_yen is not None:
        try:
            filters["min_net_profit_yen"] = int(min_net_profit_yen)
        except Exception:
            pass

    if min_reward_risk is not None:
        try:
            filters["min_reward_risk"] = float(min_reward_risk)
        except Exception:
            pass

    if allow_negative_pl is not None:
        filters["allow_negative_pl"] = bool(allow_negative_pl)

    if commission_rate is not None:
        try:
            fees["commission_rate"] = float(commission_rate)
        except Exception:
            pass

    if min_commission is not None:
        try:
            fees["min_commission"] = float(min_commission)
        except Exception:
            pass

    if slippage_rate is not None:
        try:
            fees["slippage_rate"] = float(slippage_rate)
        except Exception:
            pass

    data["filters"] = filters
    data["fees"] = fees
    _write_policy_dict(data)


# ----------------------------------------------------------------------
# タブ判定
# ----------------------------------------------------------------------
def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / pro / summary / advanced を取得。
    想定外の値が来たときは basic にフォールバック。
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "pro", "summary", "advanced") else t


# ----------------------------------------------------------------------
# メインビュー
# ----------------------------------------------------------------------
@login_required
@transaction.atomic
def settings_view(request: HttpRequest) -> HttpResponse:
    user = request.user
    tab = _get_tab(request)

    # --- UserSetting を取得/作成 --------------------------------------------
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )

    # ポリシー値を読み込み（なければ UserSetting / デフォルトで補完）
    policy_ctx = _build_policy_context()
    risk_pct = float(
        (policy_ctx.get("risk_pct") is not None and policy_ctx.get("risk_pct"))
        or (us.risk_pct or 1.0)
    )
    credit_usage_pct = float(
        (policy_ctx.get("credit_usage_pct") is not None
         and policy_ctx.get("credit_usage_pct"))
        or 70.0
    )

    # PROタブ用：学習モード・建玉・資金ルール
    learn_mode = str(policy_ctx.get("learn_mode") or "collect").lower()
    if learn_mode not in ("collect", "strict"):
        learn_mode = "collect"

    max_positions = int(policy_ctx.get("max_positions") or 5)
    max_notional_per_trade_yen = int(policy_ctx.get("max_notional_per_trade_yen") or 2_000_000)
    min_notional_per_trade_yen = int(policy_ctx.get("min_notional_per_trade_yen") or 0)
    max_total_notional_yen = int(policy_ctx.get("max_total_notional_yen") or 5_000_000)
    horizon_bd = int(policy_ctx.get("horizon_bd") or 3)
    reserve_cash_yen = int(policy_ctx.get("reserve_cash_yen") or 0)
    strict_min_rr = float(policy_ctx.get("strict_min_rr") or 1.0)
    strict_min_net_profit_yen = int(policy_ctx.get("strict_min_net_profit_yen") or 0)

    # 倍率 / ヘアカットは UserSetting 側を使う
    leverage_rakuten = us.leverage_rakuten
    haircut_rakuten = us.haircut_rakuten
    leverage_matsui = us.leverage_matsui
    haircut_matsui = us.haircut_matsui
    leverage_sbi = us.leverage_sbi
    haircut_sbi = us.haircut_sbi

    # ------------------------------------------------------------------ POST
    if request.method == "POST":
        tab = _get_tab(request)  # hidden で送っているタブ

        def parse_float(name: str, current: float | None) -> float | None:
            v = request.POST.get(name)
            if v in (None, ""):
                return current
            try:
                return float(v)
            except ValueError:
                return current

        def parse_int(name: str, current: int | None) -> int | None:
            v = request.POST.get(name)
            if v in (None, ""):
                return current
            try:
                return int(float(v))
            except ValueError:
                return current

        # ---------- basic タブ：UserSetting + ポリシー基本（risk/credit + 倍率/ヘアカット） ----------
        if tab == "basic":
            # 1トレードリスク％（UI → ポリシー＆UserSetting に反映）
            risk_pct = parse_float("risk_pct", risk_pct) or risk_pct
            us.risk_pct = risk_pct

            # 信用余力の使用上限（％）
            credit_usage_pct = parse_float("credit_usage_pct", credit_usage_pct) or credit_usage_pct

            # 倍率 / ヘアカット（UserSetting）
            leverage_rakuten = parse_float("leverage_rakuten", leverage_rakuten)
            haircut_rakuten = parse_float("haircut_rakuten", haircut_rakuten)
            leverage_matsui = parse_float("leverage_matsui", leverage_matsui)
            haircut_matsui = parse_float("haircut_matsui", haircut_matsui)
            leverage_sbi = parse_float("leverage_sbi", leverage_sbi)
            haircut_sbi = parse_float("haircut_sbi", haircut_sbi)

            us.leverage_rakuten = leverage_rakuten
            us.haircut_rakuten = haircut_rakuten
            us.leverage_matsui = leverage_matsui
            us.haircut_matsui = haircut_matsui
            us.leverage_sbi = leverage_sbi
            us.haircut_sbi = haircut_sbi
            us.save()

            # ポリシーファイルへ反映（ポリシーを真実ソースに保つ）
            _save_policy_basic_params(risk_pct=risk_pct, credit_usage_pct=credit_usage_pct)

            messages.success(request, "保存しました")
            return redirect(f"{request.path}?tab={tab}")

        # ---------- pro タブ：Collect/Strict + 建玉/資金ルール ----------
        if tab == "pro":
            learn_mode_raw = (request.POST.get("learn_mode") or learn_mode).strip().lower()
            learn_mode = "strict" if learn_mode_raw == "strict" else "collect"

            max_positions = parse_int("max_positions", max_positions) or max_positions
            max_notional_per_trade_yen = parse_int("max_notional_per_trade_yen", max_notional_per_trade_yen) or max_notional_per_trade_yen
            min_notional_per_trade_yen = parse_int("min_notional_per_trade_yen", min_notional_per_trade_yen) or min_notional_per_trade_yen
            max_total_notional_yen = parse_int("max_total_notional_yen", max_total_notional_yen) or max_total_notional_yen
            horizon_bd = parse_int("horizon_bd", horizon_bd) or horizon_bd
            reserve_cash_yen = parse_int("reserve_cash_yen", reserve_cash_yen) or reserve_cash_yen
            strict_min_rr = parse_float("strict_min_rr", strict_min_rr) or strict_min_rr
            strict_min_net_profit_yen = parse_int("strict_min_net_profit_yen", strict_min_net_profit_yen) or strict_min_net_profit_yen

            _save_policy_learning_params(
                learn_mode=learn_mode,
                max_positions=int(max_positions),
                max_notional_per_trade_yen=int(max_notional_per_trade_yen),
                min_notional_per_trade_yen=int(min_notional_per_trade_yen),
                max_total_notional_yen=int(max_total_notional_yen),
                horizon_bd=int(horizon_bd),
                reserve_cash_yen=int(reserve_cash_yen),
                strict_min_rr=float(strict_min_rr),
                strict_min_net_profit_yen=int(strict_min_net_profit_yen),
            )

            messages.success(request, "保存しました")
            return redirect(f"{request.path}?tab={tab}")

        # ---------- advanced タブ：filters / fees 編集 ----------
        if tab == "advanced":
            current = _build_policy_context()

            min_net_profit_yen = parse_int(
                "min_net_profit_yen",
                current.get("min_net_profit_yen"),
            )
            min_reward_risk = parse_float(
                "min_reward_risk",
                current.get("min_reward_risk"),
            )

            allow_raw = request.POST.get("allow_negative_pl")
            if allow_raw is None or allow_raw == "":
                allow_negative_pl = current.get("allow_negative_pl")
            else:
                allow_negative_pl = str(allow_raw).strip().lower() in ("true", "1", "on", "yes")

            commission_rate = parse_float(
                "commission_rate",
                current.get("commission_rate"),
            )
            min_commission = parse_float(
                "min_commission",
                current.get("min_commission"),
            )
            slippage_rate = parse_float(
                "slippage_rate",
                current.get("slippage_rate"),
            )

            _save_policy_advanced_params(
                min_net_profit_yen=min_net_profit_yen,
                min_reward_risk=min_reward_risk,
                allow_negative_pl=allow_negative_pl,
                commission_rate=commission_rate,
                min_commission=min_commission,
                slippage_rate=slippage_rate,
            )

            messages.success(request, "保存しました")
            return redirect(f"{request.path}?tab={tab}")

    # ----------------------------------------------------------------- GET
    brokers = compute_broker_summaries(
        user=user,
        # 証券サマリの概算でも、ポリシー由来のリスク％を使う
        risk_pct=risk_pct,
        rakuten_leverage=leverage_rakuten,
        rakuten_haircut=haircut_rakuten,
        matsui_leverage=leverage_matsui,
        matsui_haircut=haircut_matsui,
        sbi_leverage=leverage_sbi,
        sbi_haircut=haircut_sbi,
    )

    ctx = {
        "tab": tab,

        # basic
        "risk_pct": risk_pct,
        "credit_usage_pct": credit_usage_pct,
        "leverage_rakuten": leverage_rakuten,
        "haircut_rakuten": haircut_rakuten,
        "leverage_matsui": leverage_matsui,
        "haircut_matsui": haircut_matsui,
        "leverage_sbi": leverage_sbi,
        "haircut_sbi": haircut_sbi,

        # pro
        "learn_mode": learn_mode,
        "max_positions": max_positions,
        "max_notional_per_trade_yen": max_notional_per_trade_yen,
        "min_notional_per_trade_yen": min_notional_per_trade_yen,
        "max_total_notional_yen": max_total_notional_yen,
        "horizon_bd": horizon_bd,
        "reserve_cash_yen": reserve_cash_yen,
        "strict_min_rr": strict_min_rr,
        "strict_min_net_profit_yen": strict_min_net_profit_yen,

        # summary / advanced
        "brokers": brokers,
        "policy": _build_policy_context(),
    }
    return render(request, "aiapp/settings.html", ctx)