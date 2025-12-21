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
# UI 表示用のポリシーコンテキスト（ルールタブ用）
# ----------------------------------------------------------------------
def _build_policy_context() -> Dict[str, Any]:
    """
    short_aggressive.yml の中身を UI 用に薄く整形して返す。
    """
    data = _load_policy_dict()
    filters = data.get("filters") or {}
    fees = data.get("fees") or {}

    return {
        "mode": data.get("mode") or "",
        "risk_pct": data.get("risk_pct"),
        "credit_usage_pct": data.get("credit_usage_pct"),
        "min_net_profit_yen": filters.get("min_net_profit_yen"),
        "min_reward_risk": filters.get("min_reward_risk"),
        "allow_negative_pl": filters.get("allow_negative_pl"),
        "commission_rate": fees.get("commission_rate"),
        "min_commission": fees.get("min_commission"),
        "slippage_rate": fees.get("slippage_rate"),
    }


def _save_policy_basic_params(risk_pct: float, credit_usage_pct: float) -> None:
    """
    ポリシーファイル short_aggressive.yml の
    risk_pct / credit_usage_pct だけを上書き保存する。
    他の filters / fees / pro は維持。
    """
    data = _load_policy_dict()
    data["risk_pct"] = float(risk_pct)
    data["credit_usage_pct"] = float(credit_usage_pct)
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
            filters["min_net_profit_yen"] = float(min_net_profit_yen)
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
# PROタブ：Collect/Strict の “両方を保持” しつつ、選択中をアクティブ反映する
# ----------------------------------------------------------------------
def _num(v: Any, default: float) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _int(v: Any, default: int) -> int:
    try:
        if v is None:
            return int(default)
        return int(float(v))
    except Exception:
        return int(default)


def _build_pro_context(policy: Dict[str, Any]) -> Dict[str, Any]:
    pro = policy.get("pro") or {}
    if not isinstance(pro, dict):
        pro = {}

    learn_mode = str(pro.get("learn_mode") or "collect").strip().lower()
    if learn_mode not in ("collect", "strict"):
        learn_mode = "collect"

    profiles = pro.get("profiles") or {}
    if not isinstance(profiles, dict):
        profiles = {}

    # 既存 limits を “アクティブ値” として持っている前提も残す（後方互換）
    limits = policy.get("limits") or {}
    if not isinstance(limits, dict):
        limits = {}

    filters = policy.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}

    def _profile_get(name: str) -> Dict[str, Any]:
        p = profiles.get(name) or {}
        return p if isinstance(p, dict) else {}

    c = _profile_get("collect")
    s = _profile_get("strict")

    c_limits = c.get("limits") or {}
    s_limits = s.get("limits") or {}
    if not isinstance(c_limits, dict):
        c_limits = {}
    if not isinstance(s_limits, dict):
        s_limits = {}

    s_tighten = s.get("tighten") or {}
    if not isinstance(s_tighten, dict):
        s_tighten = {}

    # デフォルト（collect）
    # ─ max_positions は既存 limits から拾えるようにしておく（移行を滑らかに）
    c_max_positions = _int(c_limits.get("max_positions"), _int(limits.get("max_positions"), 5))
    c_max_notional = _int(c_limits.get("max_notional_per_trade_yen"), 2_000_000)
    c_min_notional = _int(c_limits.get("min_notional_per_trade_yen"), 0)
    c_max_total = _int(c_limits.get("max_total_notional_yen"), 5_000_000)
    c_reserve = _int(c_limits.get("reserve_cash_yen"), 0)
    c_horizon = _int(c_limits.get("horizon_bd"), _int(pro.get("horizon_bd"), 3))

    # strict のデフォルト
    s_max_positions = _int(s_limits.get("max_positions"), c_max_positions)
    s_max_notional = _int(s_limits.get("max_notional_per_trade_yen"), 1_500_000)
    s_min_notional = _int(s_limits.get("min_notional_per_trade_yen"), 0)
    s_max_total = _int(s_limits.get("max_total_notional_yen"), c_max_total)
    s_reserve = _int(s_limits.get("reserve_cash_yen"), c_reserve)
    s_horizon = _int(s_limits.get("horizon_bd"), c_horizon)

    strict_min_rr = _num(s_tighten.get("min_reward_risk"), 1.0)
    strict_min_net_profit = _int(s_tighten.get("min_net_profit_yen"), 0)

    # 画面に出す：短縮した “今効いてる値（active）” も欲しいとき用
    active_label = "Collect" if learn_mode == "collect" else "Strict"

    return {
        "pro_mode": learn_mode,
        "pro_active_label": active_label,

        "c_max_positions": c_max_positions,
        "c_max_notional_per_trade_yen": c_max_notional,
        "c_min_notional_per_trade_yen": c_min_notional,
        "c_max_total_notional_yen": c_max_total,
        "c_reserve_cash_yen": c_reserve,
        "c_horizon_bd": c_horizon,

        "s_max_positions": s_max_positions,
        "s_max_notional_per_trade_yen": s_max_notional,
        "s_min_notional_per_trade_yen": s_min_notional,
        "s_max_total_notional_yen": s_max_total,
        "s_reserve_cash_yen": s_reserve,
        "s_horizon_bd": s_horizon,

        "strict_min_rr": strict_min_rr,
        "strict_min_net_profit_yen": strict_min_net_profit,
    }


def _save_pro_params_from_post(request: HttpRequest) -> None:
    """
    PROタブで編集した値を short_aggressive.yml に保存する。
    ポイント：
    - Collect/Strict “両方の値” を pro.profiles に保持
    - 選択中（pro.learn_mode）の値を “既存の limits / filters にも同期”
      → 他サービスがまだ旧キー参照でも確実に効く
    """
    data = _load_policy_dict()

    pro = data.get("pro") or {}
    if not isinstance(pro, dict):
        pro = {}

    profiles = pro.get("profiles") or {}
    if not isinstance(profiles, dict):
        profiles = {}

    # 選択モード
    pro_mode = str(request.POST.get("pro_mode") or "collect").strip().lower()
    if pro_mode not in ("collect", "strict"):
        pro_mode = "collect"

    def p_int(name: str, default: int) -> int:
        raw = request.POST.get(name)
        if raw is None or raw == "":
            return int(default)
        try:
            return int(float(raw))
        except Exception:
            return int(default)

    def p_float(name: str, default: float) -> float:
        raw = request.POST.get(name)
        if raw is None or raw == "":
            return float(default)
        try:
            return float(raw)
        except Exception:
            return float(default)

    # 現状を基準にする（入力欠損のとき壊さない）
    ctx = _build_pro_context(data)

    # Collect
    c = profiles.get("collect") or {}
    if not isinstance(c, dict):
        c = {}
    c_limits = c.get("limits") or {}
    if not isinstance(c_limits, dict):
        c_limits = {}

    c_limits["max_positions"] = max(1, p_int("c_max_positions", ctx["c_max_positions"]))
    c_limits["max_notional_per_trade_yen"] = max(0, p_int("c_max_notional_per_trade_yen", ctx["c_max_notional_per_trade_yen"]))
    c_limits["min_notional_per_trade_yen"] = max(0, p_int("c_min_notional_per_trade_yen", ctx["c_min_notional_per_trade_yen"]))
    c_limits["max_total_notional_yen"] = max(0, p_int("c_max_total_notional_yen", ctx["c_max_total_notional_yen"]))
    c_limits["reserve_cash_yen"] = max(0, p_int("c_reserve_cash_yen", ctx["c_reserve_cash_yen"]))
    c_limits["horizon_bd"] = max(1, p_int("c_horizon_bd", ctx["c_horizon_bd"]))

    c["limits"] = c_limits
    profiles["collect"] = c

    # Strict
    s = profiles.get("strict") or {}
    if not isinstance(s, dict):
        s = {}
    s_limits = s.get("limits") or {}
    if not isinstance(s_limits, dict):
        s_limits = {}
    s_tighten = s.get("tighten") or {}
    if not isinstance(s_tighten, dict):
        s_tighten = {}

    s_limits["max_positions"] = max(1, p_int("s_max_positions", ctx["s_max_positions"]))
    s_limits["max_notional_per_trade_yen"] = max(0, p_int("s_max_notional_per_trade_yen", ctx["s_max_notional_per_trade_yen"]))
    s_limits["min_notional_per_trade_yen"] = max(0, p_int("s_min_notional_per_trade_yen", ctx["s_min_notional_per_trade_yen"]))
    s_limits["max_total_notional_yen"] = max(0, p_int("s_max_total_notional_yen", ctx["s_max_total_notional_yen"]))
    s_limits["reserve_cash_yen"] = max(0, p_int("s_reserve_cash_yen", ctx["s_reserve_cash_yen"]))
    s_limits["horizon_bd"] = max(1, p_int("s_horizon_bd", ctx["s_horizon_bd"]))

    s_tighten["min_reward_risk"] = max(0.0, p_float("strict_min_rr", ctx["strict_min_rr"]))
    s_tighten["min_net_profit_yen"] = max(0, p_int("strict_min_net_profit_yen", ctx["strict_min_net_profit_yen"]))

    s["limits"] = s_limits
    s["tighten"] = s_tighten
    profiles["strict"] = s

    pro["learn_mode"] = pro_mode
    pro["profiles"] = profiles
    data["pro"] = pro

    # -----------------------------
    # 既存のキーにも “アクティブ値” を同期（重要）
    # -----------------------------
    limits = data.get("limits") or {}
    if not isinstance(limits, dict):
        limits = {}
    filters = data.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}
    data["limits"] = limits
    data["filters"] = filters

    if pro_mode == "collect":
        active = profiles["collect"]["limits"]
        limits["max_positions"] = int(active["max_positions"])
        limits["max_notional_per_trade_yen"] = int(active["max_notional_per_trade_yen"])
        limits["min_notional_per_trade_yen"] = int(active["min_notional_per_trade_yen"])
        limits["max_total_notional_yen"] = int(active["max_total_notional_yen"])
        limits["reserve_cash_yen"] = int(active["reserve_cash_yen"])
        data["pro"]["horizon_bd"] = int(active["horizon_bd"])

        # Collect は母数優先：締め付けは “ゼロ基準” でゆるめる（Strictと差が見える）
        filters["min_reward_risk"] = 0.0
        filters["min_net_profit_yen"] = 0.0

    else:
        active = profiles["strict"]["limits"]
        limits["max_positions"] = int(active["max_positions"])
        limits["max_notional_per_trade_yen"] = int(active["max_notional_per_trade_yen"])
        limits["min_notional_per_trade_yen"] = int(active["min_notional_per_trade_yen"])
        limits["max_total_notional_yen"] = int(active["max_total_notional_yen"])
        limits["reserve_cash_yen"] = int(active["reserve_cash_yen"])
        data["pro"]["horizon_bd"] = int(active["horizon_bd"])

        tighten = profiles["strict"].get("tighten") or {}
        if not isinstance(tighten, dict):
            tighten = {}
        filters["min_reward_risk"] = float(tighten.get("min_reward_risk") or 1.0)
        filters["min_net_profit_yen"] = float(tighten.get("min_net_profit_yen") or 0.0)

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

    # 倍率 / ヘアカットは UserSetting 側を使う
    leverage_rakuten = us.leverage_rakuten
    haircut_rakuten = us.haircut_rakuten
    leverage_matsui = us.leverage_matsui
    haircut_matsui = us.haircut_matsui
    leverage_sbi = us.leverage_sbi
    haircut_sbi = us.haircut_sbi

    # PRO（Collect/Strict 両方保持 + 選択モード）
    policy_raw = _load_policy_dict()
    pro_ctx = _build_pro_context(policy_raw)

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

        # ---------- basic タブ：UserSetting + ポリシー基本 ----------
        if tab == "basic":
            risk_pct = parse_float("risk_pct", risk_pct) or risk_pct
            us.risk_pct = risk_pct

            credit_usage_pct = parse_float("credit_usage_pct", credit_usage_pct) or credit_usage_pct

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

            _save_policy_basic_params(risk_pct=risk_pct, credit_usage_pct=credit_usage_pct)

            messages.success(request, "保存しました")
            return redirect(f"{request.path}?tab={tab}")

        # ---------- pro タブ：Collect/Strict の中枢パラメータ ----------
        if tab == "pro":
            _save_pro_params_from_post(request)
            messages.success(request, "保存しました")
            # 保存後は同じタブに戻す
            return redirect(f"{request.path}?tab=pro")

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

        "risk_pct": risk_pct,
        "credit_usage_pct": credit_usage_pct,

        "leverage_rakuten": leverage_rakuten,
        "haircut_rakuten": haircut_rakuten,
        "leverage_matsui": leverage_matsui,
        "haircut_matsui": haircut_matsui,
        "leverage_sbi": leverage_sbi,
        "haircut_sbi": haircut_sbi,

        "brokers": brokers,
        "policy": _build_policy_context(),

        # PRO
        **pro_ctx,
    }
    return render(request, "aiapp/settings.html", ctx)