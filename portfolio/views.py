from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST 

# メインページ
@login_required
def main_view(request):
    return render(request, 'main.html')

# ログインページ
def login_view(request):
    error = None
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('main')
        else:
            error = "ユーザー名またはパスワードが間違っています。"
    return render(request, 'login.html', {'error': error})

# ログアウト（POSTのみ）
@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')



@login_required
def stock_list_view(request):
    """
    株リストページ（仮）
    """
    return render(request, 'stock_list.html')

@login_required
def settings_view(request):
    """
    設定ページ（仮）
    """
    return render(request, 'settings.html')