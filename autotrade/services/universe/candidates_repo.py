"""
[FILE] autotrade/services/universe_candidates_repo.py
[PATH] <project_root>/autotrade/services/universe_candidates_repo.py

このファイルは何？
- 「銘柄候補リスト」を読み込むだけの責務（Repository）を持つファイルです。
- 候補は以下の順で決まります：
  1) media/autotrade/universe_candidates.txt があればそれを読む
  2) なければ settings.AUTOTRADE_DEFAULT_CANDIDATES を使う

初心者ポイント：
- “候補の管理場所”をここに固定すると、後で迷わない。
"""

import os
from django.conf import settings


def load_candidates():
    path = os.path.join(settings.MEDIA_ROOT, "autotrade", "universe_candidates.txt")
    if os.path.exists(path):
        xs = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                xs.append(s)
        if xs:
            return xs

    return list(getattr(settings, "AUTOTRADE_DEFAULT_CANDIDATES", []))
