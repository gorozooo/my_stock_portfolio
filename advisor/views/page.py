from django.shortcuts import render

def board_page(request):
    # base.html を継承して、JS が /advisor/api/board/ を fetch して描画
    return render(request, "advisor/board.html")