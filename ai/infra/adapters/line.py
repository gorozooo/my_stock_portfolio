import os
import requests
from typing import List, Dict

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_USER_ID = os.getenv('LINE_USER_ID')

def _post(path: str, payload: Dict):
    token = LINE_CHANNEL_ACCESS_TOKEN
    if not token:
        return False, 'LINE_CHANNEL_ACCESS_TOKEN is not set'
    url = f"https://api.line.me/v2/bot{path}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        return (r.status_code == 200), (r.text if r.text else str(r.status_code))
    except Exception as e:
        return False, str(e)

# ---- テキスト（運用向け） ----
def send_ops_alert(title: str, lines: List[str]):
    if not LINE_USER_ID:
        return False, 'LINE_USER_ID is not set'
    text = f"[{title}]\n" + "\n".join(lines[:25])
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    return _post("/message/push", payload)

# ---- Flex（AIカード向け） ----
def _bubble_for_item(it: Dict) -> Dict:
    # it: {name,code,sector,score,stars,trend{d,w,m},prices{entry,tp,sl}}
    stars = '⭐️'*int(it.get('stars',1)) + '☆'*(5-int(it.get('stars',1)))
    trends = f"日:{_tri(it,'d')} 週:{_tri(it,'w')} 月:{_tri(it,'m')}"
    return {
      "type": "bubble",
      "body": {
        "type": "box", "layout": "vertical", "spacing": "sm", "contents": [
          {"type": "text", "text": f"{it['name']} ({it['code']})", "weight": "bold", "wrap": True, "size": "md"},
          {"type": "text", "text": it.get("sector",""), "size": "sm", "color": "#94a3b8"},
          {"type": "box", "layout": "baseline", "contents": [
              {"type": "text", "text": stars, "size": "sm"},
              {"type": "text", "text": f"{it['score']}点", "size": "sm", "margin": "sm"}
          ]},
          {"type": "text", "text": trends, "size": "sm"},
          {"type": "text", "text": f"目安: {it['prices']['entry']} / 利確: {it['prices']['tp']} / 損切: {it['prices']['sl']}", "size": "sm", "wrap": True}
        ]
      }
    }

def _tri(it: Dict, k: str) -> str:
    d = (it.get('trend') or {}).get(k) or it.get(f"trend_{k}")  # data shapes both supported
    return '⤴️' if d == 'up' else ('⤵️' if d == 'down' else '➡️')

def send_ai_flex(title: str, top_items: List[Dict]):
    """
    候補の上位5件をFlexで送信
    """
    if not LINE_USER_ID:
        return False, 'LINE_USER_ID is not set'
    bubbles = [_bubble_for_item(x) for x in top_items[:5]]
    payload = {
      "to": LINE_USER_ID,
      "messages": [{
        "type": "flex",
        "altText": f"{title}",
        "contents": {"type": "carousel", "contents": bubbles}
      }]
    }
    return _post("/message/push", payload)