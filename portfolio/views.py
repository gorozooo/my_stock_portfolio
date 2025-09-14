from django.shortcuts import render

def main(request):
    # まずは最小のダミーデータ（後でAIカードに差し替え）
    cards = [
        {"name": "トヨタ", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーG", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})
