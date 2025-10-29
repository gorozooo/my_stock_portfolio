from __future__ import annotations
import json, os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now as dj_now

# Boardデータをそのまま使う（再現性のためJSONも一緒に保存）
try:
    from advisor.services.board_source import build_board as build_board_service  # type: ignore
except Exception:
    build_board_service = None  # type: ignore

# 最終砦（/advisor/api/board/ と同じローカル生成フォールバック）
from advisor.views.api import _build_board_from_trends as build_from_trends  # type: ignore
from advisor.views.api import _build_board_local as build_local  # type: ignore

User = get_user_model()
JST = timezone(timedelta(hours=9))


@dataclass
class ReportAssets:
    out_dir: Path
    html_path: Path
    json_path: Path
    latest_html: Path
    latest_json: Path


def _resolve_user(user_id: Optional[int]) -> Optional[Any]:
    if user_id:
        return User.objects.filter(id=user_id).first()
    return User.objects.first()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _media_root() -> Path:
    base = getattr(settings, "MEDIA_ROOT", None)
    if not base:
        raise CommandError("MEDIA_ROOTが未設定です（settings.MEDIA_ROOT を設定してください）")
    return Path(base)


def _render_html(data: Dict[str, Any]) -> str:
    """依存ゼロの軽量テンプレ（スマホ最適）。"""
    meta = data.get("meta", {})
    theme = data.get("theme", {})
    items: List[Dict[str, Any]] = data.get("highlights", [])[:5]

    gen_at = meta.get("generated_at") or dj_now().astimezone(JST).isoformat()
    live = bool(meta.get("live", False))
    pill = "LIVE" if live else "DEMO"
    regime = meta.get("regime", {})
    trend_prob = float(regime.get("trend_prob") or 0.0)
    nikkei = regime.get("nikkei", "—")
    topix = regime.get("topix", "—")

    def esc(x):  # 極小エスケープ
        if x is None: return ""
        return str(x).replace("<","&lt;").replace(">","&gt;")

    def pct(x: Optional[float]) -> str:
        try:
            return f"{round(float(x)*100)}%"
        except Exception:
            return "--%"

    # ── CSSは最小＆内蔵（外部なし） ──
    css = """
    :root{--bg:#0b0f1a;--fg:#eaf0ff;--sub:#9fb0c9;--card:rgba(255,255,255,.05);--bd:rgba(255,255,255,.1);
          --good:#16a34a;--warn:#f59e0b;--bad:#ef4444;--acc:#2563eb}
    *{box-sizing:border-box} body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0}
    .wrap{max-width:940px;margin:0 auto;padding:16px}
    header{position:sticky;top:0;background:linear-gradient(180deg,rgba(11,15,26,.96),rgba(11,15,26,.85) 70%,transparent);
           backdrop-filter:blur(10px);border-bottom:1px solid var(--bd);z-index:10}
    .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .date{font-size:1.05rem;font-weight:700}
    .pill{padding:4px 10px;border:1px solid var(--bd);border-radius:999px;background:var(--card);font-weight:700}
    .pill.live{border-color:rgba(16,185,129,.35);background:rgba(16,185,129,.16);color:#bbf7d0}
    .pill.demo{border-color:rgba(245,158,11,.35);background:rgba(245,158,11,.16);color:#ffe3b2}
    .kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px}
    @media(max-width:720px){.kpis{grid-template-columns:1fr}}
    .kpi{padding:8px 10px;border:1px solid var(--bd);border-radius:12px;background:var(--card)}
    h2{margin:16px 0 8px;font-size:1.1rem}
    .cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    @media(max-width:840px){.cards{grid-template-columns:1fr}}
    .card{position:relative;border:1px solid var(--bd);border-radius:14px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.03));
          padding:12px}
    .code{opacity:.8;font-size:.9em}
    .seg{color:var(--sub);font-size:.9em;margin-top:2px}
    .overall{display:flex;gap:10px;align-items:center;margin:8px 0}
    .ov{font-weight:700}
    .act{padding:4px 8px;border-radius:8px;background:rgba(37,99,235,.15);border:1px solid rgba(37,99,235,.35);display:inline-block}
    ul.rea{margin:8px 0 0 1em;padding:0}
    ul.rea li{margin:.2em 0}
    .targets,.entry{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:8px}
    @media(max-width:720px){.targets,.entry{grid-template-columns:1fr}}
    .meter{height:8px;background:rgba(255,255,255,.12);border-radius:99px;overflow:hidden}
    .meter i{display:block;height:100%;width:30%;background:linear-gradient(90deg,var(--acc),#3b82f6)}
    footer{opacity:.7;margin:18px 0 40px;font-size:.9em}
    .jsonbox{margin-top:14px;padding:10px;border:1px dashed var(--bd);border-radius:12px;background:rgba(255,255,255,.03);max-height:360px;overflow:auto}
    .small{font-size:.9em;color:var(--sub)}
    """

    # ── HTML本体 ──
    html = [f"<!doctype html><html lang='ja'><meta charset='utf-8'><title>Daily Report</title><style>{css}</style><body>"]
    html.append("<header><div class='wrap'>")
    dt = datetime.fromisoformat(gen_at.replace("Z","+00:00")).astimezone(JST)
    dow = "日月火水木金土"[dt.weekday() if dt.weekday()<7 else 0]
    html.append(f"<div class='row'><div class='date'>{dt:%Y/%m/%d}（{dow}）</div>"
                f"<div class='pill {'live' if live else 'demo'}'>{pill}</div></div>")
    html.append("<div class='kpis'>")
    html.append(f"<div class='kpi'>レジーム：トレンド確率 <b>{round(trend_prob*100)}%</b>（日経{esc(nikkei)} / TOPIX{esc(topix)}）</div>")
    html.append(f"<div class='kpi'>週テーマ： " + " / ".join([f"{esc(t.get('label',''))} {round(float(t.get('score',0))*100)}点"
                                                         for t in (theme.get('top3') or [])]) + "</div>")
    html.append(f"<div class='kpi'>モデル：{esc(meta.get('model_version',''))}</div>")
    html.append("</div></div></header>")

    html.append("<main class='wrap'>")
    html.append("<h2>今日の候補（上位5件）</h2>")
    html.append("<section class='cards'>")

    for i, it in enumerate(items, 1):
        name = esc((it.get("name") or it.get("ticker") or "—"))
        tkr  = esc(it.get("ticker"))
        seg  = esc(it.get("segment") or "")
        overall = int(it.get("overall_score") or 0)
        wk = esc(it.get("weekly_trend") or "flat")
        reasons = it.get("reasons") or []
        tp = it.get("targets", {}).get("tp_price")
        sl = it.get("targets", {}).get("sl_price")
        entry = it.get("entry_price_hint")
        tp_pct = it.get("targets", {}).get("tp_pct")
        sl_pct = it.get("targets", {}).get("sl_pct")
        tp_prob = it.get("ai", {}).get("tp_prob")
        sl_prob = it.get("ai", {}).get("sl_prob")

        html.append("<article class='card'>")
        html.append(f"<div><b>{i}. {name}</b> <span class='code'>({tkr})</span></div>")
        html.append(f"<div class='seg'>{seg} ・ 週足：{wk}</div>")
        html.append(f"<div class='overall'><span class='ov'>総合 {overall} 点</span>"
                    f"<span class='small'>TP到達:{pct(tp_prob)} / SL到達:{pct(sl_prob)}</span></div>")
        html.append("<div class='act'>行動候補</div>")
        html.append("<ul class='rea'>" + "".join([f"<li>{esc(str(r))}</li>" for r in reasons]) + "</ul>")
        html.append("<div class='targets'>"
                    f"<div>🎯 目標 {pct(tp_pct)} → <b>{(tp or '-'):,}</b> 円</div>"
                    f"<div>🛑 損切 {pct(sl_pct)} → <b>{(sl or '-'):,}</b> 円</div></div>")
        html.append("<div class='entry'>"
                    f"<div>IN目安：<b>{(entry or '-'):,}</b> 円</div>"
                    f"<div class='meter'><i style='width:{max(8, overall)}%'></i></div></div>")
        html.append("</article>")

    html.append("</section>")

    # 追跡性：生データスナップショットへの導線
    html.append("<h2>スナップショット</h2>")
    html.append("<div class='jsonbox small'>この日のJSONは同フォルダに保存されています（.json）。</div>")
    html.append("</main>")

    html.append("<footer class='wrap'><div>© advisor report / 再現性ファースト（policy-driven）</div></footer>")
    html.append("</body></html>")
    return "".join(html)


def _resolve_board(user, use_cache: bool, force_service: bool) -> Dict[str, Any]:
    # 1) サービス（可能なら）
    if force_service and callable(build_board_service):
        try:
            if "use_cache" in build_board_service.__code__.co_varnames:
                return build_board_service(user, use_cache=use_cache)  # type: ignore
            return build_board_service(user)  # type: ignore
        except Exception:
            pass
    # 2) TrendResult
    try:
        data = build_from_trends(user)
        if data: return data
    except Exception:
        pass
    # 3) デモ
    return build_local(user)


class Command(BaseCommand):
    help = "日次レポートを media/advisor/reports/YYYY-MM-DD.{html,json} に保存し、latest.* も更新します。"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（JST基準、未指定は今日）")
        parser.add_argument("--user-id", type=int, default=None, help="対象ユーザーID（未指定は先頭ユーザー）")
        parser.add_argument("--no-cache", action="store_true", help="Board生成時にキャッシュを使わない")
        parser.add_argument("--service-first", action="store_true", help="board_source.build_boardを優先して使用")

    def handle(self, *args, **opts):
        jst_now = dj_now().astimezone(JST)
        if opts.get("date"):
            try:
                run_date = datetime.strptime(opts["date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date は YYYY-MM-DD 形式で指定してください")
        else:
            run_date = jst_now.date()

        user = _resolve_user(opts.get("user_id"))
        if not user:
            raise CommandError("対象ユーザーが見つかりません")

        use_cache = not bool(opts.get("no_cache"))
        force_service = bool(opts.get("service_first"))

        data = _resolve_board(user, use_cache=use_cache, force_service=force_service)
        # liveピルを確実に付与
        data.setdefault("meta", {}).setdefault("live", True)
        # 生成時刻の固定（JST）
        data["meta"]["generated_at"] = datetime.combine(run_date, datetime.min.time()).replace(tzinfo=JST).isoformat()

        root = _media_root()
        out_dir = root / "advisor" / "reports"
        _ensure_dir(out_dir)

        fname = f"{run_date.isoformat()}"
        assets = ReportAssets(
            out_dir=out_dir,
            html_path=out_dir / f"{fname}.html",
            json_path=out_dir / f"{fname}.json",
            latest_html=out_dir / "latest.html",
            latest_json=out_dir / "latest.json",
        )

        # JSON保存（再現性の核）
        with open(assets.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # HTML保存
        html = _render_html(data)
        with open(assets.html_path, "w", encoding="utf-8") as f:
            f.write(html)

        # latest.* を更新（シンボリックリンク不可環境考慮で上書きコピー）
        for src, dst in [(assets.html_path, assets.latest_html), (assets.json_path, assets.latest_json)]:
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                with open(src, "rb") as s, open(dst, "wb") as d:
                    d.write(s.read())
            except Exception:
                pass

        self.stdout.write(self.style.SUCCESS(
            f"Report generated:\n  HTML: {assets.html_path}\n  JSON: {assets.json_path}\n  Latest: {assets.latest_html}"
        ))