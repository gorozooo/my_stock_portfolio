from datetime import date
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import AutoTradeDailyState


@login_required
def dashboard(request):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)
    return render(request, "autotrade/dashboard.html", {"state": state})