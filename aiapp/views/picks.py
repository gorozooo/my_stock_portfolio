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


def _safe_float_any(v: Any) -> Optional[float]:
    """item内の値をfloatに変換（失敗時はNone）。"""
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _attach_reason_lines(data: Dict[str, Any]) -> None:
    """銘柄ごとの「理由×5＋懸念」を簡易生成して item に埋め込む。"""
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    for it in items:
        reasons: List[str] = []

        name = it.get("name_norm") or it.get("name") or ""
        sector = it.get("sector_display") or "業種不明"
        score = _safe_float_any(it.get("score_100"))
        trend = (it.get("trend_daily") or "").strip()
        slope = _safe_float_any(it.get("slope_20"))
        atr_pct = _safe_float_any(it.get("atr_14")) or _safe_float_any(it.get("atr_pct"))
        vol_ratio = _safe_float_any(it.get("vol_ratio_20d")) or _safe_float_any(it.get("volume_ratio"))
        rel_rank = _safe_float_any(it.get("rel_strength_rank"))

        entry = _safe_float_any(it.get("entry"))
        tp = _safe_float_any(it.get("tp"))
        sl = _safe_float_any(it.get("sl"))

        # 1) スコア + 業種
        if score is not None:
            reasons.append(
                f"総合スコアは {score:.0f} 点で、現状の候補の中でも上位クラスと判定されています（業種: {sector}）。"
            )
        else:
            reasons.append(f"{sector} セクターの中から、トレンドとリスク条件を満たした銘柄として抽出されています。")

        # 2) トレンド方向
        if trend:
            t_low = trend.lower()
            if t_low in ("up", "uptrend", "bull"):
                reasons.append("日足ベースで上昇トレンドと判定されており、押し目〜順張り向きの流れになっています。")
            elif t_low in ("down", "downtrend", "bear"):
                reasons.append("日足ベースでは下落トレンド寄りですが、リバウンド候補としてスコア上位に入っています。")
            elif t_low in ("flat", "range"):
                reasons.append("日足はレンジ〜もみ合い気味ですが、ブレイクが出た場合の伸びしろが期待できる位置です。")
            else:
                reasons.append(f"日足トレンド指標（{trend}）が極端に崩れておらず、方向性の整合性は保たれています。")

        # 3) 傾き（slope）
        if slope is not None:
            if slope >= 10:
                reasons.append("直近の価格の傾きがかなり強く上向きで、短期的なモメンタムがはっきり出ています。")
            elif slope >= 5:
                reasons.append("価格の傾きは緩やかな上向きで、無理のないペースでトレンドが続いている状態です。")
            elif slope > 0:
                reasons.append("価格の傾きはわずかに上向きで、崩れてはいない素直な上昇トレンドです。")
            elif slope <= -10:
                reasons.append("足元では急な下落トレンドですが、行き過ぎからの戻り候補としてピックされています。")
            else:
                reasons.append("直近の傾きは強くはないものの、急激なトレンド転換は出ていない状態です。")

        # 4) ATR（ボラティリティ）
        if atr_pct is not None:
            if atr_pct < 1.0:
                reasons.append("ATRベースの日次ボラティリティが1％未満で、値動きは比較的おだやかです。")
            elif atr_pct < 3.0:
                reasons.append("ATRベースのボラティリティが1〜3％程度で、短期トレードにちょうど良い値動きの大きさです。")
            else:
                reasons.append("ATRベースで3％を超えるボラティリティがあり、大きく動きやすい銘柄として扱われています。")

        # 5) 出来高（ボリューム）
        if vol_ratio is not None:
            if vol_ratio >= 3.0:
                reasons.append("直近出来高が20日平均の3倍以上と非常に多く、短期的な資金流入が強く出ています。")
            elif vol_ratio >= 1.5:
                reasons.append("出来高が平常時の1.5倍以上に増えており、マーケットの注目度が高まっている状態です。")
            elif vol_ratio > 0:
                reasons.append("出来高は平均付近ですが、極端な薄商いではなく売買が成立しやすい水準です。")

        # 6) Risk/Reward（Entry/TP/SL）
        if entry is not None and tp is not None and sl is not None and sl != entry:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                r = reward / risk
                reasons.append(
                    f"Entry〜SLの損切り幅に対して、TPまでの利幅はおよそ {r:.2f} R 程度を狙える設計になっています。"
                )

        # 5つにトリミング（足りない場合はそのまま）
        if len(reasons) > 5:
            reason_lines = reasons[:5]
        else:
            reason_lines = reasons

        # 懸念（atrが高い / volが急増 など簡易）
        concern = ""
        if atr_pct is not None and atr_pct >= 3.0:
            concern = "ボラティリティが高く、逆方向に振れた際の振れ幅も大きくなりやすい点には注意が必要です。"
        elif vol_ratio is not None and vol_ratio >= 3.0:
            concern = "直近の出来高急増が一時的なイベント要因の可能性もあり、出来高が細った後の反動には注意が必要です。"

        it["reason_lines"] = reason_lines
        it["reason_concern"] = concern


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
    _attach_reason_lines(data)  # ★銘柄ごとの理由×5＋懸念

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
    _attach_reason_lines(data)
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