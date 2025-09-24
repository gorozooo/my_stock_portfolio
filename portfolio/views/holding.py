# portfolio/views/holding.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from ..models import Holding
from ..forms import HoldingForm

@login_required
def holding_list(request):
    holdings = Holding.objects.filter(user=request.user).order_by("-opened_at", "-id")
    return render(request, "holdings/list.html", {"holdings": holdings})

@login_required
def holding_create(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            holding = form.save(commit=False)
            holding.user = request.user
            holding.save()
            return redirect("holding_list")
    else:
        form = HoldingForm()
    return render(request, "holdings/form.html", {"form": form})

@login_required
def holding_edit(request, pk):
    holding = get_object_or_404(Holding, pk=pk, user=request.user)
    if request.method == "POST":
        form = HoldingForm(request.POST, instance=holding)
        if form.is_valid():
            form.save()
            return redirect("holding_list")
    else:
        form = HoldingForm(instance=holding)
    return render(request, "holdings/form.html", {"form": form})