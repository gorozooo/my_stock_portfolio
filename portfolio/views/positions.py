# portfolio/views/positions.py
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from ..models import Position

@login_required
def position_list(request):
    """
    保有ポジション一覧（スマホ向けカードUI）
    """
    qs = Position.objects.filter(user=request.user).order_by("-opened_at")
    return render(request, "positions/list.html", {"positions": qs})