# portfolio/views/positions.py
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from ..models import Position

@login_required
def position_list(request):
    """保有ポジション一覧"""
    positions = Position.objects.filter(user=request.user)
    return render(request, "positions/list.html", {"positions": positions})