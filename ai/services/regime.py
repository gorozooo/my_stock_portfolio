from ai.models import TrendResult

def calculate_market_regime() -> dict:
    """
    TrendResult から日足上昇率を算出して市場のレジームを返す。
    例: {'label': '上昇', 'confidence': 84}
    """
    total = TrendResult.objects.count()
    if total == 0:
        return {'label': 'データ不足', 'confidence': 0}

    ups = TrendResult.objects.filter(dir_d='up').count()
    ratio = round(ups / total * 100, 1)

    if ratio >= 70:
        label = '上昇'
    elif ratio >= 40:
        label = '中立'
    else:
        label = '下降'

    return {'label': label, 'confidence': ratio}