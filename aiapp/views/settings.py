# aiapp/views/settings.py
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

import yaml
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


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
    short_aggressive.yml を毎回読み直して dict で返す（常に最新を見る）
    """
    path = _policy_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}
    return data


def _write_policy_dict(data: Dict[str, Any]) -> None:
    """
    dict を short_aggressive.yml に書き戻す
    """
    path = _policy_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ----------------------------------------------------------------------
# タブ判定
# ----------------------------------------------------------------------
def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / pro / summary / advanced
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower().strip()
    if t not in ("basic", "pro", "summary", "advanced"):
        return "basic"
    return t


# ----------------------------------------------------------------------
# PROポリシー：Collect/Strict をYAMLで分けて持つ（真実ソース）
# ----------------------------------------------------------------------
def _ensure_pro_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存YAMLが古い形でも、proセクションを必ず用意して返す。
    ここで“勝手に値を変える”はしない。無いものはデフォルトを置くだけ。
    """
    pro = data.get("pro")
    if not isinstance(pro, dict):
        pro = {}

    mode = pro.get("mode")
    if mode not in ("collect", "strict"):
        mode = "collect"

    # Collect / Strict の入れ物
    collect = pro.get("collect")
    strict = pro.get("strict")
    if not isinstance(collect, dict):
        collect = {}
    if not isinstance(strict, dict):
        strict = {}

    # ここは「UI表示が壊れないための最低限デフォルト」
    # ※値がYAMLにあればそれを優先（上書きしない）
    def _set_default(dct: Dict[str, Any], key: str, val: Any) -> None:
        if key not in dct or dct.get(key) is None:
            dct[key] = val

    # 建玉・資金ルール（中核）
    _set_default(collect, "max_positions", 5)
    _set_default(collect, "max_yen_per_trade", 2_000_000)
    _set_default(collect, "min_yen_per_trade", 0)
    _set_default(collect, "pro_equity_yen", 5_000_000)
    _set_default(collect, "reserve_yen", 0)
    _set_default(collect, "horizon_bd", 3)

    _set_default(strict, "max_positions", 5)
    _set_default(strict, "max_yen_per_trade", 2_000_000)
    _set_default(strict, "min_yen_per_trade", 0)
    _set_default(strict, "pro_equity_yen", 5_000_000)
    _set_default(strict, "reserve_yen", 0)
    _set_default(strict, "horizon_bd", 3)

    # Strictの締め付け（Strict時に必ず効く）
    _set_default(strict, "strict_min_rr", 1.0)
    _set_default(strict, "strict_min_net_profit_yen", 0)

    pro["mode"] = mode
    pro["collect"] = collect
    pro["strict"] = strict
    data["pro"] = pro
    return data


def _get_pro_blocks(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    data2 = _ensure_pro_schema(data)
    pro = data2.get("pro") or {}
    mode = (pro.get("mode") or "collect").strip()
    collect = pro.get("collect") or {}
    strict = pro.get("strict") or {}
    if mode not in ("collect", "strict"):
        mode = "collect"
    return mode, collect, strict


def _save_pro_policy(
    *,
    pro_mode: str,
    collect: Dict[str, Any],
    strict: Dict[str, Any],
) -> None:
    data = _load_policy_dict()
    data = _ensure_pro_schema(data)

    pro_mode = (pro_mode or "collect").strip().lower()
    if pro_mode not in ("collect", "strict"):
        pro_mode = "collect"

    data["pro"]["mode"] = pro_mode
    data["pro"]["collect"] = collect
    data["pro"]["strict"] = strict
    _write_policy_dict(data)


# ----------------------------------------------------------------------
# 既存（basic/advanced）用：UI表示コンテキスト
# ----------------------------------------------------------------------
def _build_policy_context_basic_advanced() -> Dict[str, Any]:
    """
    既存の filters/fees（ルールタブ用）を表示するだけ。
    PROタブは別で扱う（混ぜない）。
    """
    data = _load_policy_dict()
    filters = data.get("filters") or {}
    fees = data.get("fees") or {}
    if not isinstance(filters, dict):
        filters = {}
    if not isinstance(fees, dict):
        fees = {}

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
    data = _load_policy_dict()
    filters = data.get("filters") or {}
    fees = data.get("fees") or {}
    if not isinstance(filters, dict):
        filters = {}
    if not isinstance(fees, dict):
        fees = {}

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
# メインビュー
# ----------------------------------------------------------------------
@login_required
@transaction.atomic
def settings_view(request: HttpRequest) -> HttpResponse:
    user = request.user
    tab = _get_tab(request)

    # --- UserSetting を取得/作成（既存） ---
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )

    # 既存 basic/advanced 用のポリシー読み込み
    policy_ctx = _build_policy_context_basic_advanced()

    risk_pct = float(
        (policy_ctx.get("risk_pct") is not None and policy_ctx.get("risk_pct"))
        or (us.risk_pct or 1.0)
    )
    credit_usage_pct = float(
        (policy_ctx.get("credit_usage_pct") is not None and policy_ctx.get("credit_usage_pct"))
        or 70.0
    )

    # 倍率 / ヘアカット（UserSetting）
    leverage_rakuten = us.leverage_rakuten
    haircut_rakuten = us.haircut_rakuten
    leverage_matsui = us.leverage_matsui
    haircut_matsui = us.haircut_matsui
    leverage_sbi = us.leverage_sbi
    haircut_sbi = us.haircut_sbi

    # PRO（YAML真実ソース）
    raw = _load_policy_dict()
    raw = _ensure_pro_schema(raw)
    pro_mode, pro_collect, pro_strict = _get_pro_blocks(raw)

    # ------------------------------------------------------------------ POST
    if request.method == "POST":
        tab = _get_tab(request)

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

        # ---------- basic タブ（既存そのまま） ----------
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

        # ---------- PRO タブ（新規：Collect/Strict + 建玉/資金ルール） ----------
        if tab == "pro":
            # mode
            m = (request.POST.get("pro_mode") or pro_mode).strip().lower()
            if m not in ("collect", "strict"):
                m = "collect"

            # collect / strict を“別々に”更新
            # ここが肝：同じフォーム名でも接頭辞で完全分離する
            collect = dict(pro_collect or {})
            strict = dict(pro_strict or {})

            # --- 共通：建玉・資金ルール ---
            collect["max_positions"] = parse_int("c_max_positions", int(collect.get("max_positions", 5))) or int(collect.get("max_positions", 5))
            collect["max_yen_per_trade"] = parse_int("c_max_yen_per_trade", int(collect.get("max_yen_per_trade", 2_000_000))) or int(collect.get("max_yen_per_trade", 2_000_000))
            collect["min_yen_per_trade"] = parse_int("c_min_yen_per_trade", int(collect.get("min_yen_per_trade", 0))) or int(collect.get("min_yen_per_trade", 0))
            collect["pro_equity_yen"] = parse_int("c_pro_equity_yen", int(collect.get("pro_equity_yen", 5_000_000))) or int(collect.get("pro_equity_yen", 5_000_000))
            collect["reserve_yen"] = parse_int("c_reserve_yen", int(collect.get("reserve_yen", 0))) or int(collect.get("reserve_yen", 0))
            collect["horizon_bd"] = parse_int("c_horizon_bd", int(collect.get("horizon_bd", 3))) or int(collect.get("horizon_bd", 3))

            strict["max_positions"] = parse_int("s_max_positions", int(strict.get("max_positions", 5))) or int(strict.get("max_positions", 5))
            strict["max_yen_per_trade"] = parse_int("s_max_yen_per_trade", int(strict.get("max_yen_per_trade", 2_000_000))) or int(strict.get("max_yen_per_trade", 2_000_000))
            strict["min_yen_per_trade"] = parse_int("s_min_yen_per_trade", int(strict.get("min_yen_per_trade", 0))) or int(strict.get("min_yen_per_trade", 0))
            strict["pro_equity_yen"] = parse_int("s_pro_equity_yen", int(strict.get("pro_equity_yen", 5_000_000))) or int(strict.get("pro_equity_yen", 5_000_000))
            strict["reserve_yen"] = parse_int("s_reserve_yen", int(strict.get("reserve_yen", 0))) or int(strict.get("reserve_yen", 0))
            strict["horizon_bd"] = parse_int("s_horizon_bd", int(strict.get("horizon_bd", 3))) or int(strict.get("horizon_bd", 3))

            # --- Strictの締め付け（Strict時のみ必ず効く） ---
            strict["strict_min_rr"] = parse_float("s_strict_min_rr", float(strict.get("strict_min_rr", 1.0))) or float(strict.get("strict_min_rr", 1.0))
            strict["strict_min_net_profit_yen"] = parse_int("s_strict_min_net_profit_yen", int(strict.get("strict_min_net_profit_yen", 0))) or int(strict.get("strict_min_net_profit_yen", 0))

            _save_pro_policy(pro_mode=m, collect=collect, strict=strict)

            messages.success(request, "保存しました（short_aggressive.yml に反映）")
            return redirect(f"{request.path}?tab={tab}")

        # ---------- advanced タブ（既存） ----------
        if tab == "advanced":
            current = _build_policy_context_basic_advanced()

            min_net_profit_yen = parse_int("min_net_profit_yen", current.get("min_net_profit_yen"))
            min_reward_risk = parse_float("min_reward_risk", current.get("min_reward_risk"))

            allow_raw = request.POST.get("allow_negative_pl")
            if allow_raw is None or allow_raw == "":
                allow_negative_pl = current.get("allow_negative_pl")
            else:
                allow_negative_pl = str(allow_raw).strip().lower() in ("true", "1", "on", "yes")

            commission_rate = parse_float("commission_rate", current.get("commission_rate"))
            min_commission = parse_float("min_commission", current.get("min_commission"))
            slippage_rate = parse_float("slippage_rate", current.get("slippage_rate"))

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

    # GET時も最新YAMLを反映して表示
    raw2 = _load_policy_dict()
    raw2 = _ensure_pro_schema(raw2)
    pro_mode, pro_collect, pro_strict = _get_pro_blocks(raw2)

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

        # summary
        "brokers": brokers,

        # advanced（既存）
        "policy": _build_policy_context_basic_advanced(),

        # PRO（新規）
        "pro_mode": pro_mode,
        "pro_collect": pro_collect,
        "pro_strict": pro_strict,
        "policy_file_path": _policy_file_path(),
    }
    return render(request, "aiapp/settings.html", ctx)