# aiapp/views/picks.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from aiapp.models import StockMaster
from aiapp.services.policy_loader import load_short_aggressive_policy

PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"

# JPX 33業種（コード→日本語名）
JPX_SECTOR_MAP: Dict[str, str] = {
    "005": "水産・農林業", "010": "鉱業", "015": "建設業", "020": "食料品", "025": "繊維製品",
    "030": "パルプ・紙", "035": "化学", "040": "医薬品", "045": "石油・石炭製品", "050": "ゴム製品",
    "055": "ガラス・土石製品", "060": "鉄鋼", "065": "非鉄金属", "070": "金属製品", "075": "機械",
    "080": "電気機器", "085": "輸送用機器", "090": "精密機器", "095": "その他製品",
    "100": "電気・ガス業",
    "105": "陸運業", "110": "海運業", "115": "空運業", "120": "倉庫・運輸関連業",
    "125": "情報・通信業",
    "130": "卸売業", "135": "小売業",
    "140": "銀行業", "145": "証券・商品先物取引業", "150": "保険業", "155": "その他金融業",
    "160": "不動産業",
    "165": "サービス業",
}


def _load_latest_path() -> Optional[Path]:
    """最新ピックJSONの実体ファイルをフォールバック順で解決"""
    for name in ("latest.json", "latest_lite.json", "latest_full.json", "latest_synthetic.json"):
        p = PICKS_DIR / name
        if p.exists() and p.is_file():
            return p
    return None


def _load_picks() -> Dict[str, Any]:
    """壊れていてもスキーマを崩さず返す"""
    path = _load_latest_path()
    base = {"meta": {"generated_at": None, "mode": None, "count": 0}, "items": [], "_path": None}
    if not path:
        return base
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    meta = dict(data.get("meta") or {})
    items = list(data.get("items") or [])
    # 互換：トップレベルに mode がある旧構造も拾う
    meta.setdefault("mode", data.get("mode"))
    meta.setdefault("count", len(items))
    data = {"meta": meta, "items": items, "_path": str(path)}
    return data


def _is_etf(code: str) -> bool:
    """ざっくりETF判定（先頭1xxxが多い）。厳密化は後でOK"""
    try:
        return code and code[0] == "1"
    except Exception:
        return False


def _sector_from_master(sm: Optional[StockMaster]) -> Optional[str]:
    if not sm:
        return None
    # sector_name が入っていれば最優先
    if sm.sector_name:
        return sm.sector_name
    # sector_code → 名称へ
    if sm.sector_code and sm.sector_code in JPX_SECTOR_MAP:
        return JPX_SECTOR_MAP[sm.sector_code]
    return None


def _enrich_with_master(data: Dict[str, Any]) -> None:
    """itemsを銘柄名/業種/価格の表示用に正規化"""
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    codes = {str(x.get("code", "")).strip() for x in items if x.get("code")}
    masters = {
        sm.code: sm
        for sm in StockMaster.objects.filter(code__in=codes).only("code", "name", "sector_name", "sector_code")
    }

    for it in items:
        code = str(it.get("code", "")).strip()
        sm = masters.get(code)

        # name
        name = it.get("name")
        if not name or name == code:
            it["name"] = (sm.name if sm else code) or code
        it.setdefault("name_norm", it["name"])

        # sector display（フォールバック順）
        sector_json = it.get("sector") or it.get("sector_name")
        sector_mst = _sector_from_master(sm)
        if _is_etf(code):
            sector_disp = "ETF/ETN"
        else:
            sector_disp = sector_json or sector_mst or "業種不明"
        it["sector_display"] = sector_disp

        # last_close 防御
        val = it.get("last_close")
        try:
            it["last_close"] = float(val) if val is not None else None
        except Exception:
            it["last_close"] = None


def _format_updated_label(meta: Dict[str, Any], path_str: Optional[str], count: int) -> str:
    """
    表示用の最終更新ラベル：
      1) meta.generated_at があればそれを表示
      2) 無ければ JSONファイルの mtime を表示
    例: 2025/11/09 01:23　6件 / FORCE_LITE
    """
    mode = meta.get("mode") or "lite"
    raw_ts = (meta.get("generated_at") or "").strip() if isinstance(meta.get("generated_at"), str) else None

    if raw_ts:
        ts_label = raw_ts
    else:
        # ファイル mtime にフォールバック
        if path_str:
            p = Path(path_str)
            if p.exists():
                ts_label = timezone.localtime(
                    timezone.make_aware(
                        timezone.datetime.fromtimestamp(p.stat().st_mtime)
                    )
                ).strftime("%Y/%m/%d %H:%M")
            else:
                ts_label = timezone.localtime().strftime("%Y/%m/%d %H:%M")
        else:
            ts_label = timezone.localtime().strftime("%Y/%m/%d %H:%M")
    return f"{ts_label}　{count}件 / {str(mode).upper()}"


def _build_zero_reason(est_pl: float, est_loss: float) -> str:
    """
    0株になったときの“理由テキスト”を生成する。
    数式そのものは出さないけど、
      - R値
      - 想定利益の大きさ
      - 利益がマイナス/ゼロ
    を見て、どこがNGなのかをはっきりさせる。
    """
    reasons: List[str] = []

    # 想定利益がマイナス or 0
    if est_pl <= 0:
        reasons.append("TPまで到達しても想定利益がプラスにならないため。")

    # R値（利益/損失）
    if est_pl > 0 and est_loss > 0:
        r = est_pl / est_loss
        if r < 1.0:
            reasons.append(f"R値（利益÷損失）が {r:.2f} で、短期ルールの下限 1.0 を下回るため。")

    # 利益の絶対額が小さすぎる
    if est_pl > 0 and est_pl < 2000:
        reasons.append(
            f"想定利益が {int(round(est_pl)):,} 円と小さく、手数料やスリッページを考えると短期トレードとして狙う価値が低いため。"
        )

    if not reasons:
        # どの条件も“ギリギリ”で落ちているケースなど
        return "短期ルール（R値・コスト・最低利益）のいずれかが基準を満たしていないため。"
    return " ".join(reasons)


def _attach_zero_reasons(data: Dict[str, Any]) -> None:
    """
    item ごとに「楽天/松井が0株になっている理由」を計算して
    reason_rakuten / reason_matsui として埋め込む。
    """
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    for it in items:
        # 無い場合は 0 扱い
        qty_r = float(it.get("qty_rakuten") or 0)
        qty_m = float(it.get("qty_matsui") or 0)
        est_pl_r = float(it.get("est_pl_rakuten") or 0)
        est_pl_m = float(it.get("est_pl_matsui") or 0)
        est_loss_r = float(it.get("est_loss_rakuten") or 0)
        est_loss_m = float(it.get("est_loss_matsui") or 0)

        reason_r = ""
        reason_m = ""

        if qty_r <= 0:
            reason_r = _build_zero_reason(est_pl_r, est_loss_r)
        if qty_m <= 0:
            reason_m = _build_zero_reason(est_pl_m, est_loss_m)

        it["reason_rakuten"] = reason_r
        it["reason_matsui"] = reason_m


# ===== ここから B用の「ルール通過チェック」 =====


def _load_policy_thresholds() -> Dict[str, Any]:
    """
    short_aggressive.yml から、フィルター系のしきい値だけを取り出す。
    値が無い場合はデフォルト（2000円 / R=1.0 / マイナス許容しない）。
    """
    data = load_short_aggressive_policy() or {}
    filters = data.get("filters") or {}

    min_net_profit = filters.get("min_net_profit_yen", 2000)
    min_reward_risk = filters.get("min_reward_risk", 1.0)
    allow_negative_pl = filters.get("allow_negative_pl", False)

    try:
        min_net_profit = float(min_net_profit)
    except Exception:
        min_net_profit = 2000.0

    try:
        min_reward_risk = float(min_reward_risk)
    except Exception:
        min_reward_risk = 1.0

    allow_negative_pl = bool(allow_negative_pl)

    return {
        "min_net_profit_yen": min_net_profit,
        "min_reward_risk": min_reward_risk,
        "allow_negative_pl": allow_negative_pl,
    }


def _attach_pass_checks(data: Dict[str, Any]) -> None:
    """
    数量が > 0 の銘柄について、「なぜルールを通過しているか」の
    チェックリストを pass_checks_rakuten / pass_checks_matsui に埋め込む。
    """
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    thresholds = _load_policy_thresholds()
    min_net_profit = thresholds["min_net_profit_yen"]
    min_rr = thresholds["min_reward_risk"]
    allow_negative_pl = thresholds["allow_negative_pl"]

    def build_checks(qty: float, est_pl: float, est_loss: float) -> List[str]:
        checks: List[str] = []
        if qty <= 0:
            return checks

        # 1) 純利益プラス or マイナス許容
        if est_pl > 0:
            checks.append(f"✅ 純利益プラス（約 {int(round(est_pl)):,} 円）")
        else:
            if allow_negative_pl:
                checks.append(
                    f"✅ 純利益マイナス {int(round(est_pl)):,} 円だが、ポリシーでマイナス許容ON"
                )
            else:
                # ここに来るケースはほぼ無い想定だが、一応文言だけ。
                checks.append("✅ 純利益が0円近辺（コスト込みでギリギリ許容）")

        # 2) 想定利益 >= 最低純利益
        if est_pl > 0:
            checks.append(
                f"✅ 想定利益 {int(round(est_pl)):,} 円 ≥ 最低純利益 {int(min_net_profit):,} 円"
            )

        # 3) R値 >= 最低R
        if est_pl > 0 and est_loss > 0:
            r = est_pl / est_loss
            checks.append(f"✅ R値 {r:.2f} ≥ 最低R {min_rr:.2f}")

        return checks

    for it in items:
        qty_r = float(it.get("qty_rakuten") or 0)
        qty_m = float(it.get("qty_matsui") or 0)
        est_pl_r = float(it.get("est_pl_rakuten") or 0)
        est_pl_m = float(it.get("est_pl_matsui") or 0)
        est_loss_r = float(it.get("est_loss_rakuten") or 0)
        est_loss_m = float(it.get("est_loss_matsui") or 0)

        it["pass_checks_rakuten"] = build_checks(qty_r, est_pl_r, est_loss_r)
        it["pass_checks_matsui"] = build_checks(qty_m, est_pl_m, est_loss_m)


# ===== ここまで B用 =====


# ===== ここから C：行動メモリとの「相性」連携 =====

def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _bucket_atr_pct(atr: Optional[float]) -> str:
    if atr is None:
        return "ATR:不明"
    if atr < 1.0:
        return "ATR:〜1%"
    if atr < 2.0:
        return "ATR:1〜2%"
    if atr < 3.0:
        return "ATR:2〜3%"
    return "ATR:3%以上"


def _bucket_slope(slope: Optional[float]) -> str:
    if slope is None:
        return "傾き:不明"
    if slope < 0:
        return "傾き:下向き"
    if slope < 5:
        return "傾き:緩やかな上向き"
    if slope < 10:
        return "傾き:強めの上向き"
    return "傾き:急騰寄り"


def _load_behavior_memory_json(user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    行動メモリ（latest_behavior_memory_u{user}.json）を読み込む。
    見つからなければ None。
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior" / "memory"

    # ユーザー別 → なければ all
    candidates: List[Path] = []
    if user_id:
        candidates.append(behavior_dir / f"latest_behavior_memory_u{user_id}.json")
    candidates.append(behavior_dir / "latest_behavior_memory_uall.json")

    target: Optional[Path] = None
    for p in candidates:
        if p.exists() and p.is_file():
            target = p
            break

    if not target:
        return None

    try:
        text = target.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _build_affinity_label(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    StatBucket.to_dict() 相当の dict から
    「◎/○/△/×/？」＋ラベル文字列を作る。
    """
    if not stats:
        return {
            "symbol": "？",
            "trials": 0,
            "win_rate": None,
            "label": "？ データなし",
        }

    trials = int(stats.get("trials") or 0)
    win_rate = stats.get("win_rate")
    if win_rate is None:
        symbol = "？"
        label = "？ データなし"
    else:
        try:
            win_rate_f = float(win_rate)
        except Exception:
            win_rate_f = 0.0

        # ランク判定
        if trials >= 5 and win_rate_f >= 60.0:
            symbol = "◎"
        elif trials >= 3 and win_rate_f >= 50.0:
            symbol = "○"
        elif trials >= 3:
            symbol = "△"
        elif trials >= 1 and win_rate_f < 30.0:
            symbol = "×"
        elif trials >= 1:
            symbol = "△"
        else:
            symbol = "？"

        if trials > 0:
            label = f"{symbol} {win_rate_f:.1f}%（{trials}戦）"
        else:
            label = "？ データなし"

        win_rate = win_rate_f

    return {
        "symbol": symbol,
        "trials": trials,
        "win_rate": win_rate,
        "label": label,
    }


def _attach_behavior_affinity(data: Dict[str, Any], user_id: Optional[int]) -> None:
    """
    行動メモリから「相性」情報を item.affinity に付与する。

    affinity = {
      "sector": {...},
      "atr": {...},
      "slope": {...},
      "trend": {...},
    }
    """
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    memory = _load_behavior_memory_json(user_id=user_id)
    if not memory:
        return

    total_trades = int(memory.get("total_trades") or 0)
    if total_trades <= 0:
        return

    sector_stats = memory.get("sector") or {}
    atr_stats = memory.get("atr_bucket") or {}
    slope_stats = memory.get("slope_bucket") or {}
    trend_stats = memory.get("trend_daily") or {}

    for it in items:
        # セクターキー
        sec_key = str(it.get("sector_display") or "(未分類)")
        sec_aff = _build_affinity_label(sector_stats.get(sec_key))

        # ATRバケット
        atr_raw = _safe_float(it.get("atr_14") or it.get("atr_pct"))
        atr_bucket = _bucket_atr_pct(atr_raw)
        atr_aff = _build_affinity_label(atr_stats.get(atr_bucket))

        # 傾きバケット
        slope_raw = _safe_float(it.get("slope_20") or it.get("slope"))
        slope_bucket = _bucket_slope(slope_raw)
        slope_aff = _build_affinity_label(slope_stats.get(slope_bucket))

        # トレンド方向
        trend_key = str(it.get("trend_daily") or "不明")
        trend_aff = _build_affinity_label(trend_stats.get(trend_key))

        it["affinity"] = {
            "sector": sec_aff,
            "atr": atr_aff,
            "slope": slope_aff,
            "trend": trend_aff,
        }


# ===== ここまで C =====


def picks(request: HttpRequest) -> HttpResponse:
    # LIVE/DEMO 状態（基本は常に LIVE、?mode=demo のときだけ DEMO 扱い）
    qmode = request.GET.get("mode")
    if qmode == "demo":
        is_demo = True
    elif qmode == "live":
        is_demo = False
    else:
        # パラメータ指定なし → 常に LIVE
        is_demo = False

    data = _load_picks()
    _enrich_with_master(data)
    _attach_zero_reasons(data)
    _attach_pass_checks(data)  # ★ルール通過の内訳を付与

    # ★ 行動メモリから「相性」情報を付与（ログイン時のみ）
    user_id: Optional[int] = None
    if request.user and request.user.is_authenticated:
        user_id = request.user.id
    _attach_behavior_affinity(data, user_id=user_id)

    meta = data.get("meta") or {}
    count = meta.get("count") or len(data.get("items") or [])
    updated_label = _format_updated_label(meta, data.get("_path"), count)

    # sizing / ポリシーから渡ってきた meta をそのまま使う
    lot_size = int(meta.get("lot_size") or 100)
    try:
        risk_pct = float(meta.get("risk_pct")) if meta.get("risk_pct") is not None else 1.0
    except Exception:
        risk_pct = 1.0

    ctx = {
        "items": data.get("items") or [],
        "updated_label": updated_label,
        "mode_label": "LIVE/DEMO",
        "is_demo": is_demo,
        # ラベル用の lot_size / risk_pct は JSON の meta に合わせる
        "lot_size": lot_size,
        "risk_pct": risk_pct,
    }
    return render(request, "aiapp/picks.html", ctx)


def picks_json(request: HttpRequest) -> HttpResponse:
    data = _load_picks()
    _enrich_with_master(data)
    _attach_zero_reasons(data)
    _attach_pass_checks(data)  # JSON側にも載せておく

    # JSON側にも相性を載せる（必要ならフロントで使える）
    user_id: Optional[int] = None
    if request.user and request.user.is_authenticated:
        user_id = request.user.id
    _attach_behavior_affinity(data, user_id=user_id)

    if not data:
        raise Http404("no picks")
    # 内部用メタは出さない
    data.pop("_path", None)
    return JsonResponse(data, safe=True, json_dumps_params={"ensure_ascii": False, "indent": 2})


# ===== ここから C：シミュレ（紙トレ）保存 =====


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


@login_required
@require_POST
def picks_simulate(request: HttpRequest) -> HttpResponse:
    """
    AI Picks のカードから「シミュレ」ボタンで送られてきた内容を
    /media/aiapp/simulate/YYYYMMDD.jsonl に 1行追記するだけのシンプル紙トレ。
    """
    user = request.user

    code = (request.POST.get("code") or "").strip()
    name = (request.POST.get("name") or "").strip()
    mode = (request.POST.get("mode") or "demo").strip()  # demo / live

    entry = _parse_float(request.POST.get("entry"))
    qty_rakuten = _parse_float(request.POST.get("qty_rakuten"))
    qty_matsui = _parse_float(request.POST.get("qty_matsui"))
    required_cash_rakuten = _parse_float(request.POST.get("required_cash_rakuten"))
    required_cash_matsui = _parse_float(request.POST.get("required_cash_matsui"))
    est_pl_rakuten = _parse_float(request.POST.get("est_pl_rakuten"))
    est_pl_matsui = _parse_float(request.POST.get("est_pl_matsui"))
    est_loss_rakuten = _parse_float(request.POST.get("est_loss_rakuten"))
    est_loss_matsui = _parse_float(request.POST.get("est_loss_matsui"))
    price_date = (request.POST.get("price_date") or "").strip() or None

    now_jst = timezone.localtime()
    day = now_jst.strftime("%Y%m%d")

    record = {
        "ts": now_jst.isoformat(),
        "user_id": user.id,
        "username": user.get_username(),
        "mode": mode,
        "code": code,
        "name": name,
        "entry": entry,
        "qty_rakuten": qty_rakuten,
        "qty_matsui": qty_matsui,
        "required_cash_rakuten": required_cash_rakuten,
        "required_cash_matsui": required_cash_matsui,
        "est_pl_rakuten": est_pl_rakuten,
        "est_loss_rakuten": est_loss_rakuten,
        "est_pl_matsui": est_pl_matsui,
        "est_loss_matsui": est_loss_matsui,
        "price_date": price_date,
        "source": "ai_picks",
    }

    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
    sim_dir.mkdir(parents=True, exist_ok=True)
    out_path = sim_dir / f"{day}.jsonl"

    try:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        messages.success(request, f"シミュレに登録しました：{code} {name}")
    except Exception as e:
        messages.error(request, f"シミュレ保存に失敗しました：{e}")

    # ひとまず picks に戻す（モードはURLクエリで切り替え）
    return redirect("aiapp:picks")