# -*- coding: utf-8 -*-
from __future__ import annotations
from django.core.management.base import BaseCommand
from datetime import datetime
import os
import json
from collections import Counter

MEDIA = os.path.join(os.getcwd(), "media")
FEEDBACK_PATH = os.path.join(MEDIA, "feedback.jsonl")
EXAMPLE_PATH = os.path.join(MEDIA, "persona/gorozooo_examples.jsonl")
STATS_PATH = os.path.join(MEDIA, "persona/gorozooo_stats.json")

class Command(BaseCommand):
    help = "gorozooo人格の自己学習。feedback.jsonlから👍を抽出して学習データを更新。"

    def handle(self, *args, **opts):
        os.makedirs(os.path.dirname(EXAMPLE_PATH), exist_ok=True)

        if not os.path.exists(FEEDBACK_PATH):
            self.stdout.write(self.style.WARNING("feedback.jsonl が存在しません。"))
            return

        # === 既存の学習済みテキストをロード ===
        existing_texts = set()
        if os.path.exists(EXAMPLE_PATH):
            with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        existing_texts.add(json.loads(line)["text"])
                    except Exception:
                        continue

        # === feedback.jsonl をスキャン ===
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]

        # 👍だけ抽出
        good = [x for x in lines if x.get("feedback") in ("👍", "+1")]
        added = []
        tones = Counter()

        for g in good[-50:]:  # 最新50件まで見る
            text = g.get("text", "").strip()
            if not text or text in existing_texts:
                continue
            rec = {
                "timestamp": g.get("timestamp"),
                "mode": g.get("mode"),
                "text": text
            }
            added.append(rec)
            tones.update(_extract_emojis(text))

        if not added:
            self.stdout.write(self.style.WARNING("新しい👍フィードバックはありません。"))
            return

        # === 書き込み ===
        with open(EXAMPLE_PATH, "a", encoding="utf-8") as f:
            for rec in added:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # === 統計 ===
        stats = {
            "updated": datetime.now().isoformat(),
            "added": len(added),
            "total": len(existing_texts) + len(added),
            "emoji_freq": dict(tones.most_common(10)),
        }
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(
            f"✅ {len(added)}件の新しいフィードバックを取り込みました！"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"📊 現在の絵文字上位: {', '.join([f'{k}×{v}' for k,v in tones.most_common(5)])}"
        ))


# ---------- 補助関数 ----------
def _extract_emojis(text: str):
    """絵文字の出現頻度をカウント（感情トーン学習用）"""
    import regex
    emojis = regex.findall(r'\p{Emoji}', text)
    c = Counter()
    for e in emojis:
        c[e] += 1
    return c