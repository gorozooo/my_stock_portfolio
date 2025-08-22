from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.shortcuts import render

@login_required
def main_view(request):
    return render(request, "main.html")

def login_view(request):
    if request.user.is_authenticated:
        return redirect("main")
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("main")
        messages.error(request, "ユーザー名またはパスワードが違います。")
    return render(request, "auth_login.html")

def logout_view(request):
    logout(request)
    return redirect("login")

def stock_list_view(request):
    return render(request, 'stock_list.html')

def cash_view(request):
    return render(request, 'cash.html')

def realized_view(request):
    return render(request, 'realized.html')

def settings_view(request):
    return render(request, 'settings.html')