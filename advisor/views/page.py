from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def board_page(request):
    return render(request, "advisor/board.html")

@login_required
def watch_page(request):
    return render(request, "advisor/watch.html")