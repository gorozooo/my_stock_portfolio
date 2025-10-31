# advisor/services/notify.py
from __future__ import annotations
import os, json, time
from typing import Iterable, List, Optional
import requests

LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"
LINE_MULTICAST_API = "https://api.line.me/v2/bot/message/multicast"

def _get_token() -> Optional[str]:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

def _get_user_ids() -> List[str]:
    raw = os.getenv("LINE_TO_USER_IDS", "") or ""
    # カンマ区切り想定：Ucxxx,Udxxx,...
    return [u.strip() for u in raw.split(",") if u.strip()]

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

def _post(url: str, token: str, payload: dict) -> requests.Response:
    return requests.post(url, headers=_headers(token), data=json.dumps(payload), timeout=15)

def _require_env_or_raise():
    token = _get_token()
    uids = _get_user_ids()
    if not token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定です（.env / 環境変数を確認）")
    if not uids:
        raise RuntimeError("LINE_TO_USER_IDS が未設定です（カンマ区切りで1件以上）")
    return token, uids

def push_line_message(to_user_id: str, text: str) -> None:
    """
    単一ユーザーにPush。失敗時は例外を投げる。
    """
    token = _get_token()
    if not token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定です")
    payload = {"to": to_user_id, "messages": [{"type": "text", "text": text}]}
    r = _post(LINE_PUSH_API, token, payload)
    if r.status_code >= 300:
        raise RuntimeError(f"LINE push failed: HTTP {r.status_code} {r.text}")

def push_multicast(text: str, *, user_ids: Optional[Iterable[str]] = None) -> int:
    """
    複数ユーザーに一括送信。成功件数を返す。
    user_ids を省略→ 環境変数 LINE_TO_USER_IDS を使用。
    """
    token = _get_token()
    uids = list(user_ids) if user_ids is not None else _get_user_ids()
    if not token or not uids:
        # 片方でも無ければ0件扱い（運用しやすいよう例外にしない）
        return 0

    # LINEの仕様上はmulticastを推奨（最大500?）。少数ならpushをループでもOK。
    # ここはシンプルに push で1件ずつ（エラーも個別に分かる）
    sent = 0
    for uid in uids:
        try:
            push_line_message(uid, text)
            sent += 1
            time.sleep(0.1)  # 送信間隔を少し空ける（スパム防止）
        except Exception:
            # ログが欲しければprintにする or 将来Hook
            pass
    return sent

def diag_env() -> str:
    """自己診断メッセージ（管理コマンドで --why 時などに表示）"""
    token = _get_token()
    uids = _get_user_ids()
    parts = []
    parts.append(f"TOKEN={'set' if token else 'MISSING'}")
    parts.append(f"UIDS={len(uids)}")
    return f"[LINE diag] {' '.join(parts)}"