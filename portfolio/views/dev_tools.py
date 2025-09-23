# portfolio/views/dev_tools.py
from __future__ import annotations
import os, re
from pathlib import Path
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required

PATTERNS = [
    re.compile(r"Avg\(\s*['\"]qty['\"]\s*\)"),
    re.compile(r"Avg\(\s*['\"]fee['\"]\s*\)"),
]

@login_required
def scan_avg(request):
    """
    ブラウザから /dev/scan-avg/ にアクセスすると、
    プロジェクト内の `Avg('qty')` / `Avg('fee')` を grep して一覧表示。
    DEBUG=True でのみ動作。
    """
    if not settings.DEBUG:
        return HttpResponseForbidden("DEBUG=False なので無効です")

    root = Path(settings.BASE_DIR)  # プロジェクトルート
    targets = []
    for dirpath, dirnames, filenames in os.walk(root / "portfolio"):
        # venv や __pycache__ は除外
        if "venv" in dirpath or "__pycache__" in dirpath or ".git" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines, start=1):
                for pat in PATTERNS:
                    if pat.search(line):
                        # 前後も少し表示
                        s = max(1, i-2)
                        e = min(len(lines), i+2)
                        snippet = "\n".join(f"{n:4d}: {lines[n-1]}" for n in range(s, e+1))
                        targets.append((str(p.relative_to(root)), i, snippet))
                        break

    if not targets:
        html = "<h1>残骸は見つかりませんでした 🎉</h1>"
        return HttpResponse(html)

    parts = ["<h1>Avg('qty') / Avg('fee') のヒット一覧</h1>"]
    for file, lineno, snip in targets:
        parts.append(f"<h3>{file}:{lineno}</h3><pre style='background:#0b1020;color:#e6edff;padding:10px;border-radius:8px;overflow:auto'>{snip}</pre>")
    return HttpResponse("\n".join(parts))