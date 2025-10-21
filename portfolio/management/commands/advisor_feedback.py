# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from datetime import datetime
import json
import os

MEDIA = os.path.join(os.getcwd(), "media")
LOG_PATH = os.path.join(MEDIA, "feedback.jsonl")

class Command(BaseCommand):
    help = "LINE経由のフィードバック（👍👎修正）を記録"

    def add_arguments(self, parser):
        parser.add_argument("--mode", type=str, default="")
        parser.add_argument("--feedback", type=str, default="")  # +1, -1, edit
        parser.add_argument("--comment", type=str, default="")
        parser.add_argument("--text", type=str, default="")
        parser.add_argument("--score", type=float, default=0)
        parser.add_argument("--stance", type=str, default="")

    def handle(self, *args, **opts):
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        data = {
            "timestamp": datetime.now().isoformat(),
            "mode": opts["mode"],
            "feedback": opts["feedback"],
            "comment": opts["comment"],
            "score": opts["score"],
            "stance": opts["stance"],
            "text": opts["text"],
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        self.stdout.write(self.style.SUCCESS(f"Feedback saved: {data['feedback']}"))