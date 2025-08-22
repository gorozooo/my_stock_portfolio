from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

# メインページ
@login_required
def main_view(request):
    return render(request, 'main.html')

# ログインページ
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('main')
        else:
            error = "ユーザー名かパスワードが違います"
            return render(request, 'login.html', {'error': error})
    return render(request, 'login.html')

# ログアウト
@login_required
def logout_view(request):
    logout(request)
    return redirect('login')