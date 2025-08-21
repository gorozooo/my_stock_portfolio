from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm

# 下タブに出すページ一覧
BOTTOM_NAV = [
    {"name": "ホーム", "icon": "🏠", "url_name": "main"},
    {"name": "株", "icon": "📊", "url_name": "stock_list"},
    {"name": "設定", "icon": "⚙️", "url_name": "setting"},
]

# --- ログイン処理 ---
def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect("main")  # ログイン成功後に main へ
    else:
        form = AuthenticationForm()
    return render(request, "portfolio/login.html", {"form": form})

# --- メインページ ---
@login_required(login_url='/login/')
def main(request):
    jst_now = timezone.localtime(timezone.now())
    last_update = jst_now.strftime("%Y.%m.%d %H:%M")
    return render(request, "portfolio/main.html", {
        "bottom_nav_items": BOTTOM_NAV,
        "last_update": last_update,
        "current_page": "main",
    })

# --- 株ページ ---
@login_required(login_url='/login/')
def stock_list(request):
    last_update = timezone.localtime(timezone.now())
    return render(request, "portfolio/stock_list.html", {
        "bottom_nav_items": BOTTOM_NAV,
        "last_update": last_update,
        "current_page": "stock_list",
    })

# --- 設定ページ ---
@login_required(login_url='/login/')
def setting(request):
    last_update = timezone.localtime(timezone.now())
    return render(request, "portfolio/setting.html", {
        "bottom_nav_items": BOTTOM_NAV,
        "last_update": last_update,
        "current_page": "setting",
    })
