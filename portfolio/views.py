from django.shortcuts import render
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm

def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect("main")  # ログイン成功後 main へ
    else:
        form = AuthenticationForm()
    return render(request, "login.html", {"form": form})

PAGES = {
    "main": {"name": "ホーム", "icon": "🏠", "url_name": "main"},
    "stock_list": {"name": "株", "icon": "📊", "url_name": "stock_list"},
    #"cash_list": {"name": "キャッシュ", "icon": "💰", "url_name": "cash_list"},
    #"realized_list": {"name": "実現損益", "icon": "📈", "url_name": "realized_list"},
    "setting": {"name": "設定", "icon": "⚙️", "url_name": "setting"},
}

@login_required(login_url='/login/')
def main(request):
    jst_now = timezone.localtime(timezone.now())
    last_update = jst_now.strftime("%Y.%m.%d %H:%M")

    return render(request, 'portfolio/main.html', {
        'PAGES': PAGES,               # ← 追加
        'last_update': last_update,
        'current_page': 'main',
    })

@login_required(login_url='/login/')
def stock_list(request):
    last_update = timezone.localtime(timezone.now())

    return render(request, 'portfolio/stock_list.html', {
        'PAGES': PAGES,               # ← 追加
        'last_update': last_update,
        'current_page': 'stock_list',
    })

@login_required(login_url='/login/')
def setting(request):
    last_update = timezone.localtime(timezone.now())

    return render(request, 'portfolio/setting.html', {
        'PAGES': PAGES,               # ← 追加
        'last_update': last_update,
        'current_page': 'setting',
    })

