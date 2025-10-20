# management/commands/update_persona.py
from django.core.management.base import BaseCommand
from portfolio.models_advisor import AdvisorPersona, CommentFeedback
from openai import OpenAI
import os, datetime as dt

class Command(BaseCommand):
    help = "ユーザーのフィードバックから好み要約を更新"

    def handle(self, *args, **opts):
        client = OpenAI()
        for p in AdvisorPersona.objects.all():
            qs = CommentFeedback.objects.filter(user_id=p.user_id).order_by("-created_at")[:50]
            if not qs: continue
            bullets = "\n".join([f"- {x.comment_text[:80]} | rating={x.rating}" for x in qs])
            prompt = (
              "以下はユーザーの反応履歴です。"
              "好み（トーン/絵文字/リスク姿勢）を3行で要約し、"
              "最後に tone/calm|casual|energetic, emoji_level(0-3), risk(cautious|neutral|aggressive)"
              " をJSONで返してください。\n" + bullets
            )
            try:
                res = client.chat.completions.create(
                    model=os.getenv("AI_COMMENT_MODEL","gpt-4o-mini"),
                    messages=[{"role":"system","content":"日本語で短く"},
                              {"role":"user","content":prompt}],
                    temperature=0.3, max_tokens=200
                )
                text = res.choices[0].message.content.strip()
                # 超ザックリ抽出（本番は正規JSONを推奨）
                p.preference_summary = text[:600]
                # ヒューリスティックに微調整
                avg = sum(x.rating for x in qs)/len(qs)
                p.quality_score = 0.7*p.quality_score + 0.3*avg
                p.save(update_fields=["preference_summary","quality_score","updated_at"])
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"{p.user_id}: {e}"))