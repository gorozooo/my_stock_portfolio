from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)


# -------------------------
# small helpers
# -------------------------
def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _as_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def _safe_str(x: Any) -> str:
    try:
        if x is None:
            return ""
        return str(x)
    except Exception:
        return ""


def _today_local() -> date:
    try:
        return timezone.localdate()
    except Exception:
        return timezone.now().date()


def _safe_json_dumps(d: Any) -> str:
    try:
        return json.dumps(d, ensure_ascii=False, sort_keys=True)
    except Exception:
        try:
            return str(d)
        except Exception:
            return "<unserializable>"


def _guess_topic_from_text(text: str) -> str:
    """
    雑でもいいので「テーマ名」を推定する（後で強化する前提）。
    - 目的：AI BRIEF が “主語” を持つための材料
    """
    t = (text or "").strip()
    if not t:
        return ""

    rules = [
        ("半導体", ["半導体", "NVIDIA", "エヌビディア", "TSMC", "SOX"]),
        ("銀行", ["銀行", "金利", "利上げ", "利下げ", "国債", "YCC"]),
        ("為替", ["ドル円", "円安", "円高", "為替", "USDJPY"]),
        ("自動車", ["自動車", "トヨタ", "EV", "BYD", "テスラ", "Tesla"]),
        ("商社", ["商社", "バフェット", "総合商社"]),
        ("防衛", ["防衛", "安全保障", "地政学"]),
        ("原油", ["原油", "WTI", "OPEC"]),
        ("暗号資産", ["ビットコイン", "BTC", "暗号資産", "仮想通貨"]),
        ("決算", ["決算", "業績", "上方修正", "下方修正", "ガイダンス"]),
        ("政策", ["日銀", "FRB", "FOMC", "CPI", "雇用統計"]),
    ]
    low = t.lower()
    for topic, kws in rules:
        for k in kws:
            if k.lower() in low:
                return topic
    return ""


def _project_root_guess() -> str:
    """
    /home/gorozooo/my_stock_portfolio のようなプロジェクトルートを “雑に” 推定。
    - manage.py がある前提
    """
    try:
        here = os.path.abspath(os.getcwd())
        # いまのカレントがどこでも、上に辿って manage.py を探す
        cur = here
        for _ in range(10):
            if os.path.exists(os.path.join(cur, "manage.py")):
                return cur
            nxt = os.path.dirname(cur)
            if nxt == cur:
                break
            cur = nxt
        return here
    except Exception:
        return os.path.abspath(os.getcwd())


def _append_file_log(line: str) -> None:
    """
    logger設定に依存しない“強制ログ”
    - 基本: <project>/logs/ai_brief_ctx.log
    - ダメなら: /tmp/ai_brief_ctx.log
    """
    root = _project_root_guess()
    p1 = os.path.join(root, "logs")
    p2 = "/tmp"

    for base in (p1, p2):
        try:
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, "ai_brief_ctx.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")
            return
        except Exception:
            continue


# -------------------------
# data builders
# -------------------------
def build_market_state_from_news_trends(news_trends: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    home_news_trends の payload を “AIが食える形” に圧縮する。
    """
    out: Dict[str, Any] = {"news_top": [], "themes": [], "events": []}

    try:
        if not news_trends or not isinstance(news_trends, dict):
            return out

        # sectors -> themes
        sectors = news_trends.get("sectors") or []
        themes: List[Dict[str, Any]] = []
        if isinstance(sectors, list):
            counts: List[int] = []
            for s in sectors:
                if isinstance(s, dict):
                    counts.append(_as_int(s.get("count"), 0))
            mx = max(counts) if counts else 0

            for s in sectors[:8]:
                if not isinstance(s, dict):
                    continue
                name = _safe_str(s.get("sector")).strip()
                cnt = _as_int(s.get("count"), 0)
                if not name:
                    continue
                strength = (float(cnt) / float(mx)) if mx > 0 else 0.0
                themes.append({"name": name, "strength": strength, "count": cnt})
        out["themes"] = themes

        # items -> news_top
        items = news_trends.get("items") or []
        news_top: List[Dict[str, Any]] = []
        if isinstance(items, list):
            for it in items[:8]:
                if not isinstance(it, dict):
                    continue
                title = _safe_str(it.get("title")).strip()
                if not title:
                    continue
                source = _safe_str(it.get("source")).strip()
                url = _safe_str(it.get("link") or it.get("url")).strip()
                host = _safe_str(it.get("host")).strip()

                topic = _guess_topic_from_text(title) or _guess_topic_from_text(source)
                news_top.append(
                    {
                        "title": title,
                        "source": source,
                        "url": url,
                        "host": host,
                        "topic": topic,
                        "sentiment": "",
                    }
                )
        out["news_top"] = news_top

        return out
    except Exception:
        return out


def build_behavior_state_from_realized(user) -> Dict[str, Any]:
    """
    “学習の前段”としての行動状態を、まずは RealizedTrade から作る。
    """
    out: Dict[str, Any] = {"last_7d": {"trades": 0, "pnl_sum": 0.0}, "deviation": {"count": 0, "last_reason": ""}}

    try:
        from portfolio.models import RealizedTrade  # type: ignore

        today = _today_local()
        start = today - timedelta(days=7)

        qs = RealizedTrade.objects.filter(user=user, trade_at__gte=start)

        agg = qs.aggregate(
            cnt=Coalesce(Count("id"), Value(0)),
            pnl=Coalesce(Sum("cashflow"), Value(0)),
        )

        out["last_7d"]["trades"] = int(agg.get("cnt") or 0)
        out["last_7d"]["pnl_sum"] = float(agg.get("pnl") or 0.0)

        return out
    except Exception:
        return out


def build_user_state_from_settings(user) -> Dict[str, Any]:
    """
    UserSetting から “自分の縛り” を取る。

    ✅ ここが今回の狙い撃ちポイント：
    - year_goal_total / year_goal_by_broker を ctx に入れて、trade_setting.html と100%一致させる
    - 許容損失（円）を「全体」ではなく「楽天」「SBI+松井」グループ別に出す
    """
    out: Dict[str, Any] = {
        "equity": 0,
        "risk_pct": 0.0,
        "risk_yen": None,
        "mode_period": "",
        "mode_aggr": "",
        # ---- goals (trade_setting.html と一致させる) ----
        "goal_year_total": 0,
        "goal_year_by_broker": {},
        # ---- per-group risk (楽天 / SBI+松井) ----
        "risk_groups": {},
    }

    try:
        from portfolio.models import UserSetting  # type: ignore

        setting, _ = UserSetting.objects.get_or_create(user=user)

        # --- 基本（従来どおり） ---
        equity = _as_float(getattr(setting, "account_equity", 0), 0.0)
        risk_pct = _as_float(getattr(setting, "risk_pct", 0.0), 0.0)

        risk_yen = None
        if equity > 0 and risk_pct > 0:
            risk_yen = int(round(equity * (risk_pct / 100.0)))

        out["equity"] = int(round(equity))
        out["risk_pct"] = float(risk_pct) if risk_pct else 0.0
        out["risk_yen"] = risk_yen

        out["mode_period"] = _safe_str(getattr(setting, "mode_period", "")).strip()
        out["mode_aggr"] = _safe_str(getattr(setting, "mode_aggr", "")).strip()

        # --- 年目標（trade_setting.html の保存先をそのまま反映） ---
        goal_total = _as_int(getattr(setting, "year_goal_total", 0), 0)
        goal_by_broker_raw = getattr(setting, "year_goal_by_broker", {}) or {}

        goal_by_broker: Dict[str, int] = {}
        if isinstance(goal_by_broker_raw, dict):
            for k, v in goal_by_broker_raw.items():
                kk = _safe_str(k).strip().upper()
                if not kk:
                    continue
                vv = _as_int(v, 0)
                if vv > 0:
                    goal_by_broker[kk] = int(vv)

        out["goal_year_total"] = int(goal_total) if goal_total > 0 else 0
        out["goal_year_by_broker"] = goal_by_broker

        # --- 許容損失を「楽天」「SBI+松井」に分割（全体は使わない表示にする） ---
        # グループの“口座残高”は compute_broker_summaries の数値から作る：
        # equity_yen = cash_yen + stock_acq_value（＝設定画面の「現物（特定）評価額」と合わせる）
        risk_groups: Dict[str, Dict[str, Any]] = {}

        try:
            # 遅延 import（循環/起動コスト回避）
            from aiapp.services.broker_summary import compute_broker_summaries  # type: ignore

            # settings.py と同じパラメータ（UserSetting 由来）
            brokers = compute_broker_summaries(
                user=user,
                risk_pct=float(risk_pct) if risk_pct else 0.0,
                rakuten_leverage=float(getattr(setting, "leverage_rakuten", 0) or 0),
                rakuten_haircut=float(getattr(setting, "haircut_rakuten", 0) or 0),
                matsui_leverage=float(getattr(setting, "leverage_matsui", 0) or 0),
                matsui_haircut=float(getattr(setting, "haircut_matsui", 0) or 0),
                sbi_leverage=float(getattr(setting, "leverage_sbi", 0) or 0),
                sbi_haircut=float(getattr(setting, "haircut_sbi", 0) or 0),
            )

            # broker code -> equity_yen
            broker_eq: Dict[str, int] = {}
            for b in brokers or []:
                try:
                    code = _safe_str(getattr(b, "code", "")).strip().upper()
                    cash_yen = _as_int(getattr(b, "cash_yen", 0), 0)

                    # ★ここが修正点：
                    # 以前: stock_eval_value（評価額）を使っていて許容損失が膨らむ
                    # 今回: settings画面の「現物（特定）評価額」に合わせて stock_acq_value を使う
                    stock_acq = _as_int(getattr(b, "stock_acq_value", 0), 0)

                    if code:
                        eq = int(cash_yen + stock_acq)
                        broker_eq[code] = eq if eq > 0 else 0
                except Exception:
                    continue

            def _group_sum(codes: List[str]) -> int:
                s = 0
                for c in codes:
                    s += int(broker_eq.get(str(c).upper(), 0))
                return int(s)

            # グループ定義（AI BRIEF 側の view と一致）
            g_r_eq = _group_sum(["RAKUTEN"])
            g_s_eq = _group_sum(["SBI", "MATSUI"])

            def _risk_yen_from_eq(eq_yen: int) -> Optional[int]:
                if eq_yen <= 0 or risk_pct <= 0:
                    return None
                return int(round(float(eq_yen) * (float(risk_pct) / 100.0)))

            risk_groups = {
                "rakuten": {
                    "label": "楽天",
                    "brokers": ["RAKUTEN"],
                    "equity_yen": int(g_r_eq),
                    "risk_yen": _risk_yen_from_eq(int(g_r_eq)),
                },
                "sbi_matsui": {
                    "label": "SBI+松井",
                    "brokers": ["SBI", "MATSUI"],
                    "equity_yen": int(g_s_eq),
                    "risk_yen": _risk_yen_from_eq(int(g_s_eq)),
                },
            }

        except Exception:
            # ここで落としてAI BRIEF全体を止めない（最悪、従来どおり全体だけになる）
            risk_groups = {}

        out["risk_groups"] = risk_groups

        return out
    except Exception:
        return out


def build_portfolio_state_from_assets(assets: Dict[str, Any]) -> Dict[str, Any]:
    """
    home_assets の結果（assets payload）を、AIが扱いやすい形で“要点だけ”抜き出す。
    """
    out: Dict[str, Any] = {
        "realized_ytd": 0.0,
        "realized_mtd": 0.0,
        "goal_year_total": 0,
        "brokers": [],
    }

    try:
        realized = (assets or {}).get("realized") or {}
        ytd = realized.get("ytd") or {}
        mtd = realized.get("mtd") or {}

        out["realized_ytd"] = float(ytd.get("total") or 0.0)
        out["realized_mtd"] = float(mtd.get("total") or 0.0)

        goals = (assets or {}).get("goals") or {}
        out["goal_year_total"] = int(goals.get("year_total") or 0)

        pace = (assets or {}).get("pace") or {}
        rows = pace.get("by_broker_rows") or []
        brokers: List[Dict[str, Any]] = []
        if isinstance(rows, list):
            for r in rows:
                if not isinstance(r, dict):
                    continue
                brokers.append(
                    {
                        "broker": _safe_str(r.get("broker")).strip(),
                        "label": _safe_str(r.get("label")).strip(),
                        "ytd": _as_float(r.get("ytd"), 0.0),
                    }
                )
        out["brokers"] = brokers
        return out
    except Exception:
        return out


def build_ml_candidates_stub(user) -> List[Dict[str, Any]]:
    """
    A段階では “銘柄候補の推論” はまだつなげない。
    """
    _ = user
    return []


def build_brief_context(
    *,
    user,
    assets: Dict[str, Any],
    news_trends: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    AI BRIEF が食べる「素材」1枚を生成する。
    """
    d = _today_local()
    as_of = timezone.now().isoformat()

    user_state = build_user_state_from_settings(user)
    portfolio_state = build_portfolio_state_from_assets(assets)
    behavior_state = build_behavior_state_from_realized(user)
    market_state = build_market_state_from_news_trends(news_trends)
    ml_candidates = build_ml_candidates_stub(user)

    constraints: Dict[str, Any] = {
        "hard_rules": [
            "逆指値を先に置けないなら入らない",
            "迷ったらサイズ半分",
        ],
        "today_focus": [],
    }

    ctx: Dict[str, Any] = {
        "as_of": as_of,
        "date": d.isoformat(),
        "user_state": user_state,
        "portfolio_state": portfolio_state,
        "behavior_state": behavior_state,
        "market_state": market_state,
        "ml_candidates": ml_candidates,
        "constraints": constraints,
    }

    return ctx


def log_brief_context(ctx: Dict[str, Any]) -> None:
    """
    ログに“素材”を出す
    - logger が死んでても /logs/ai_brief_ctx.log に必ず残す
    """
    try:
        line = f"[AI_BRIEF_CTX] {timezone.now().isoformat()} { _safe_json_dumps(ctx) }"
    except Exception:
        line = f"[AI_BRIEF_CTX] {timezone.now().isoformat()} <dump_failed>"

    # 1) まずは強制ファイルログ
    _append_file_log(line)

    # 2) logger も一応投げる（生きてれば拾われる）
    try:
        logger.info("%s", line)
    except Exception:
        pass