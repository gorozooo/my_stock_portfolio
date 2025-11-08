# aiapp/management/commands/picks_build.py
from __future__ import annotations
import os, json, math, time, tempfile, shutil, datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.conf import settings

# 既存サービス／モデルを使う（無ければフェイルセーフあり）
from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features

try:
    # 既存のスコア関数があれば利用
    from aiapp.models.scoring import score_sample  # (feat_df, mode, horizon) -> float(0-100想定)
    HAVE_SCORE_SAMPLE = True
except Exception:
    HAVE_SCORE_SAMPLE = False

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR  = MEDIA_ROOT / "aiapp" / "picks"
CACHE_DIR  = MEDIA_ROOT / "aiapp" / "cache"
LOCK_FILE  = Path("/tmp/aiapp_picks_build.lock")

DEFAULT_HORIZON = "short"       # short / mid / long
DEFAULT_MODE    = "aggressive"  # aggressive / normal / defensive
DEFAULT_TONE    = "friendly"    # friendly / calm etc.

MIN_BARS = 80                   # 特徴量計算の最低本数（短期は180日読み＋NaN除去の余裕）
MAX_WORKERS = int(os.getenv("AIAPP_BUILD_WORKERS", "8"))

def _ts_jst():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))

def _ensure_dirs():
    PICKS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _percentile_clip(values, lo=1, hi=99):
    # 分位で0-1にスケール（外れ値はクリップ）
    if not values:
        return []
    xs = sorted(values)
    def at(p):
        if p <= 0:  return xs[0]
        if p >= 100: return xs[-1]
        k = (len(xs)-1) * p/100.0
        f = math.floor(k); c = math.ceil(k)
        if f == c: return xs[int(k)]
        return xs[f] + (xs[c]-xs[f])*(k-f)
    v_lo, v_hi = at(lo), at(hi)
    if v_hi == v_lo:
        return [0.5 for _ in values]
    return [min(1.0, max(0.0, (v - v_lo)/(v_hi - v_lo))) for v in values]

def _confidence_from_features(feat_last, entry, tp, sl):
    """
    AI信頼度（0–5）の簡易算出：
      - 指標安定性（直近ウィンドウの移動平均傾き/一貫性）を簡易 proxy として利用（あれば）
      - エントリーからTP/SLまでの距離（Rレンジ）を妥当化（狭すぎ/広すぎ減点）
    既存の“仮想エントリー学習”が後で入れば、そちらの結果で上書きする想定。
    """
    base = 3.0

    # 距離バランス
    try:
        r_tp = abs(tp - entry)
        r_sl = abs(entry - sl)
        if r_sl <= 0 or r_tp <= 0:
            dist_score = 0.0
        else:
            ratio = r_tp / r_sl
            # 目安：TP:SL が 1.2〜2.5 の範囲が良い
            if ratio < 0.8:   dist_score = 0.0
            elif ratio < 1.2: dist_score = 0.5
            elif ratio < 2.5: dist_score = 1.0
            elif ratio < 3.5: dist_score = 0.6
            else:             dist_score = 0.3
    except Exception:
        dist_score = 0.0

    # 指標安定性（ダミー：RSI/ROC/%K が揃っていれば少し加点）
    stab = 0.0
    try:
        good = 0
        for k in ("rsi", "roc_10", "stoch_k"):
            if k in feat_last and not (feat_last[k] is None or (isinstance(feat_last[k], float) and math.isnan(feat_last[k]))):
                good += 1
        stab = (good / 3.0)  # 0〜1
    except Exception:
        pass

    score = base + 1.2*dist_score + 0.8*stab  # ざっくり 2.5〜5.0レンジ
    score = max(0.0, min(5.0, score))
    return score

def _stars_from_conf(conf):
    # ⭐️濫発抑制：4.7未満は☆5にしない
    if conf >= 4.7: return 5.0
    # 0.25刻みで丸め
    return round(conf*4)/4

def _compute_one(code: str, horizon: str, mode: str):
    """
    1銘柄の特徴量→スコア→EP/TP/SL等を算出。失敗時はNone。
    """
    try:
        df = get_prices(code, 180)
        if df is None or len(df) < MIN_BARS:
            return None
        feat = compute_features(df)
        if feat is None or feat.empty:
            return None
        last = feat.iloc[-1].to_dict()

        # 価格と目安（既存featuresに entry/ tp/ sl があればそれを尊重）
        price = float(getattr(df["close"], "iloc", df["close"])[-1])
        entry = float(last.get("entry", price))
        tp    = float(last.get("tp",    price * 1.05))
        sl    = float(last.get("sl",    price * 0.96))

        # スコア（0–100に後で分位スケールを適用するので一旦素点）
        if HAVE_SCORE_SAMPLE:
            raw = float(score_sample(feat, mode=mode, horizon=horizon))
        else:
            # 簡易合成（RSI/ROC/%K + 傾き + VWAP乖離を重みづけ）
            rsi = float(last.get("rsi", 50.0))
            roc = float(last.get("roc_10", 0.0))
            stk = float(last.get("stoch_k", 50.0))
            slope = float(last.get("ema20_slope", 0.0))
            vwap_dev = float(last.get("vwap_dev", 0.0))
            raw = (0.22*(rsi/100.0) + 0.22*max(0.0, roc/5.0) + 0.22*(stk/100.0) +
                   0.18*max(0.0, slope/price*100.0) + 0.16*max(0.0, 1.0 - abs(vwap_dev)))
            raw = raw * 100.0

        # 信頼度（0–5）
        conf = _confidence_from_features(last, entry, tp, sl)
        stars = _stars_from_conf(conf)

        return {
            "code": code,
            "price": round(price, 3),
            "entry": round(entry, 3),
            "tp":    round(tp, 3),
            "sl":    round(sl, 3),
            "score_raw": raw,      # 後で分位スケール→score_total
            "confidence": conf,    # 0-5
            "stars": stars,        # 表示用
            "feat_last": last,     # 理由文生成のため保持
        }
    except Exception:
        return None

def _reason_lines(item):
    f = item.get("feat_last", {})
    lines = []
    # 数字＋やさしい説明（親しみトーン）
    rsi = f.get("rsi")
    if rsi is not None and not (isinstance(rsi, float) and math.isnan(rsi)):
        txt = f"RSI {int(round(rsi))}"
        if rsi >= 70: txt += "（強め・過熱気味）"
        elif rsi >= 55: txt += "（買い優勢）"
        elif rsi <= 30: txt += "（売られ気味）"
        lines.append(txt)

    macd = f.get("macd_hist")
    if macd is not None and not (isinstance(macd, float) and math.isnan(macd)):
        sign = "+" if macd >= 0 else ""
        lines.append(f"MACDヒスト {sign}{round(macd, 3)}（トレンドの追い風）" if macd >= 0 else f"MACDヒスト {sign}{round(macd,3)}（逆風気味）")

    vdev = f.get("vwap_dev")
    if vdev is not None and not (isinstance(vdev, float) and math.isnan(vdev)):
        pct = round(float(vdev)*100.0, 2) if abs(vdev) < 2 else round(float(vdev), 2)
        # vwap_dev が “率”で来るor“比”で来る環境の差異を吸収
        if abs(pct) < 1.0:
            pct = round(float(vdev)*100.0, 2)
        sign = "+" if pct >= 0 else ""
        lines.append(f"VWAP乖離 {sign}{pct}%（行き過ぎ注意）" if abs(pct) > 3 else f"VWAP乖離 {sign}{pct}%")

    c5 = f.get("ret_5d")
    if c5 is not None and not (isinstance(c5, float) and math.isnan(c5)):
        sign = "+" if c5 >= 0 else ""
        lines.append(f"直近5日 {sign}{round(c5*100.0,2)}%")

    atr = f.get("atr")
    if atr is not None and not (isinstance(atr, float) and math.isnan(atr)):
        price = item.get("price", 0) or 1
        pct = round(atr/price*100.0, 1)
        lines.append(f"ボラ目安 ATR={round(atr,1)}円（株価比 {pct}%）")

    if not lines:
        lines = ["指標の揃いが良く、短期の追い風が出ています"]
    return lines[:5]

def _concerns(item):
    f = item.get("feat_last", {})
    lst = []
    rsi = f.get("rsi")
    if rsi is not None and rsi >= 75:
        lst.append("RSIが高めで押し戻されやすいです")
    vdev = f.get("vwap_dev")
    if vdev is not None and abs(vdev) > 0.05:
        lst.append("短期の乖離が大きく、振れが出やすいです")
    if not lst:
        return []
    return lst[:2]

class Command(BaseCommand):
    help = "Build top-10 picks snapshot as JSON (all JPX universe) for the given horizon/mode."

    def add_arguments(self, parser):
        parser.add_argument("--horizon", default=DEFAULT_HORIZON, choices=["short","mid","long"])
        parser.add_argument("--mode", default=DEFAULT_MODE, choices=["aggressive","normal","defensive"])
        parser.add_argument("--tone", default=DEFAULT_TONE)
        parser.add_argument("--topn", type=int, default=10)

    def handle(self, *args, **opts):
        horizon = opts["horizon"]
        mode    = opts["mode"]
        tone    = opts["tone"]
        topn    = int(opts["topn"])

        _ensure_dirs()

        # 二重起動ロック
        if LOCK_FILE.exists():
            mtime = dt.datetime.fromtimestamp(LOCK_FILE.stat().st_mtime)
            # 10分以上前のロックは壊れている扱いで無視
            if (dt.datetime.now() - mtime).total_seconds() < 600:
                self.stdout.write(self.style.WARNING("[picks_build] another build is running; exit 202"))
                return
        try:
            LOCK_FILE.write_text(str(os.getpid()))
        except Exception:
            pass

        t0 = time.time()
        self.stdout.write(self.style.NOTICE(f"[picks_build] start {horizon}/{mode}"))

        # ユニバース（JPX全銘柄）
        qs = StockMaster.objects.all().values_list("code","name","sector33")
        universe = [(c,n,s or "") for c,n,s in qs]

        # 並列で収集
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(_compute_one, code, horizon, mode): (code,name,sector) for code,name,sector in universe}
            for fu in as_completed(futs):
                code,name,sector = futs[fu]
                item = fu.result()
                if item is None: 
                    continue
                item["code"] = code
                item["name"] = name
                item["sector33"] = sector
                results.append(item)

        if not results:
            # 何も取れなかった場合は空のスナップショットを作る（ビューを止めないため）
            snap = {
                "ts": _ts_jst().isoformat(),
                "mode": mode, "horizon": horizon, "tone": tone,
                "universe": len(universe),
                "version": "picks-v3.1",
                "items": [],
                "metrics": {},
            }
        else:
            # 分位スケーリングで 0-100 に整形（p99クリップ）
            raw = [r["score_raw"] for r in results]
            scaled01 = _percentile_clip(raw, 1, 99)
            for r, s01 in zip(results, scaled01):
                r["score_total"] = round(100.0 * s01, 1)
                r["reasons"]  = _reason_lines(r)
                r["concerns"] = _concerns(r)

            # 上位を選定（score_totalで降順）
            results.sort(key=lambda x: x.get("score_total", 0.0), reverse=True)
            top = results[:topn]

            snap = {
                "ts": _ts_jst().isoformat(),
                "mode": mode, "horizon": horizon, "tone": tone,
                "universe": len(universe),
                "version": "picks-v3.1",
                "items": [{
                    "code": it["code"],
                    "name": it["name"],
                    "sector33": it.get("sector33") or "",
                    "asof": dt.date.today().isoformat(),
                    "price": it["price"],
                    "entry": it["entry"],
                    "tp":    it["tp"],
                    "sl":    it["sl"],
                    "qty":   None,           # 表示側の sizing で数量を決定（口座資金が必要）
                    "capital": None,
                    "exp_pl": None,
                    "exp_loss": None,
                    "score_total": it["score_total"],   # 0–100
                    "confidence": round(it["confidence"], 3),  # 0–5
                    "stars": it["stars"],               # 表示丸め済
                    "reasons": it["reasons"],
                    "concerns": it["concerns"],
                } for it in top],
                "metrics": {
                    "score_p99": max([x["score_total"] for x in results]) if results else None,
                    "score_median": sorted([x["score_total"] for x in results])[len(results)//2] if results else None,
                },
            }

        # 原子的に latest_* を置換
        fname_latest = f"latest_{horizon}_{mode}.json"
        tmp = PICKS_DIR / (fname_latest + ".tmp")
        out = PICKS_DIR / fname_latest
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out)

        dur = time.time() - t0
        self.stdout.write(self.style.SUCCESS(f"[picks_build] done items={len(snap.get('items',[]))} dur={round(dur,1)}s"))
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass