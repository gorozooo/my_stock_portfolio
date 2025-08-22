from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

# ==========================
# メインページ
# ==========================
@login_required
def main_view(request):
    """
    ログイン済みユーザー用のメインページ
    """
    return render(request, 'main.html')


# ==========================
# ログインページ
# ==========================
def login_view(request):
    """
    ログインページ（カスタム版）
    認証失敗時にはエラーメッセージを返す
    """
    error = None

    if request.method == 'POST':
        username = request.POST.get('username')  # getで安全に取得
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('main')  # ログイン成功後はメインページへ
        else:
            error = "ユーザー名またはパスワードが間違っています。"

    return render(request, 'login.html', {'error': error})


# ==========================
# ログアウト処理
# ==========================
@login_required
def logout_view(request):
    """
    ログアウト処理
    """
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