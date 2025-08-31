from .models import BottomTab

def get_bottom_tabs():
    """BottomTab + SubMenu を dict 形式で返す"""
    tabs = BottomTab.objects.prefetch_related("submenus").order_by("order")
    tab_list = []
    for tab in tabs:
        tab_list.append({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon,
            "url_name": tab.url_name,
            "link_type": tab.link_type,
            "order": tab.order,
            "submenus_list": [
                {
                    "id": sm.id,
                    "name": sm.name,
                    "url": sm.url,
                    "link_type": getattr(sm, "link_type", "view"),
                    "order": sm.order
                }
                for sm in tab.submenus.all().order_by("order")
            ]
        })
    return tab_list

import yfinance as yf

def get_current_price(ticker):
    """
    現在株価を取得する
    ticker: 証券コード (例: "7203")
    """
    try:
        # 日本株は .T を付ける
        stock = yf.Ticker(f"{ticker}.T")
        data = stock.history(period="1d")
        if not data.empty:
            return round(data["Close"].iloc[-1], 2)  # 終値
    except Exception as e:
        print("株価取得エラー:", e)
    return None