# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.timezone import now


# ===== 設定 =====
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"

# 探索優先順位（上から順に試す）
CANDIDATES = [
    "latest_lite.json",
    "latest_full.json",
    "latest.json",            # ← ← ← あなたのsymlinkをここで拾う
    "latest_synthetic.json",
]

# ファイル名パターン（バックアップ保険：最新のスナップショットを拾う）
GLOB_PATTERNS = [
    "*_short_aggressive.json",
    "*_short_aggressive_lite.json",
    "*_short_aggressive_full.json",
    "*_short_aggressive_synthetic.json",
    "picks_*.json",
]


def _debug(msg: str) -> None:
    # 本番でもINFO相当を見たいのでprintにしています（gunicornの標準出力へ）
    print(f"[picks-view] {msg}")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        # シンボリックリンクも実体へ解決
        real = path.resolve()
        data = json.loads(real.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        _debug(f"read error: {path} -> {e}")
        return None


def _find_snapshot() -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """
    表示用スナップショットを探す：
      1) 固定候補（lite/full/latest/synthetic）
      2) パターン一致で一番新しいファイル
    """
    if not PICKS_DIR.exists():
        _debug(f"PICKS_DIR not found: {PICKS_DIR}")
        return None, None

    # 1) 固定候補
    for name in CANDIDATES:
        p = PICKS_DIR / name
        d = _load_json(p)
        if d and isinstance(d.get("items"), list):
            _debug(f"hit candidate: {name} items={len(d['items'])}")
            return p, d

    # 2) パターン一致の中から最終更新が最新のもの
    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    for pat in GLOB_PATTERNS:
        for p in PICKS_DIR.glob(pat):
            try:
                m = p.stat().st_mtime
                if m > latest_mtime:
                    latest_mtime = m
                    latest_path = p
            except OSError:
                continue

    if latest_path:
        d = _load_json(latest_path)
        if d and isinstance(d.get("items"), list):
            _debug(f"hit glob: {latest_path.name} items={len(d['items'])}")
            return latest_path, d

    _debug("no snapshot found")
    return None, None


def _build_view_model(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    テンプレに渡す簡易ViewModel。
    data スキーマは picks_build が出す JSON を想定（items/ts/style/horizon など）
    """
    items = data.get("items", [])
    meta = {
        "style": data.get("style", "aggressive"),
        "horizon": data.get("horizon", "short"),
        "mode": data.get("mode", "LIVE/DEMO"),
        "ts": data.get("ts") or data.get("timestamp"),
    }

    # 最終更新の表示テキスト（tsが無ければサーバ現在時刻）
    if meta["ts"]:
        last_updated = meta["ts"]
    else:
        last_updated = now().strftime("%Y/%m/%d %H:%M")

    return {
        "items": items,
        "meta": meta,
        "last_updated": last_updated,
    }


def picks(request: HttpRequest) -> HttpResponse:
    """
    /aiapp/picks/ 画面
     - スナップショット優先順でJSONを探し、見つかったものを表示
     - 見つからなければ「0件」メッセージ
    """
    path, data = _find_snapshot()

    if not data:
        # 何も無い場合でもテンプレは空で成立する
        ctx = {
            "items": [],
            "meta": {"style": "aggressive", "horizon": "short", "mode": "LIVE/DEMO", "ts": None},
            "last_updated": "",
            "info_msg": "候補が0件です。直近のスナップショットが存在しません。",
            "latest_json_url": f"{settings.MEDIA_URL}aiapp/picks/latest.json?ts={int(time.time())}",
        }
        _debug("render empty list")
        return render(request, "aiapp/picks.html", ctx)

    vm = _build_view_model(data)
    # 参考用ログ
    _debug(
        f"render {path.name} items={len(vm['items'])} "
        f"style={vm['meta'].get('style')} horizon={vm['meta'].get('horizon')}"
    )
    return render(request, "aiapp/picks.html", vm)