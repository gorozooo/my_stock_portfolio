# -*- coding: utf-8 -*-
from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from aiapp.services.snapshot import load_snapshot


def _nocache(resp: HttpResponse) -> HttpResponse:
    # Safari等の強いキャッシュ対策（HTML/JSONどちらにも付与）
    resp["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


@require_GET
def picks(request: HttpRequest) -> HttpResponse:
    """
    10銘柄カードの表示ビュー（サーバサイド描画）。
    スナップショットは「lite > full > latest > synthetic」の優先順で自動選択。
    synthetic は price/entry/tp/sl が欠けることがあるが、仕様上そのまま表示する。
    """
    data, kind, path = load_snapshot()

    ctx = {
        "items": data.get("items", []),
        "meta": data.get("meta", {}),
        # 画面右上バッジ等で使えるよう、どのファイルを読んだかを渡す
        "snapshot_kind": kind,
        "snapshot_path": str(path) if path else None,
    }
    resp = render(request, "aiapp/picks.html", ctx)
    return _nocache(resp)