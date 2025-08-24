from pathlib import Path

# =============================
# 基本ディレクトリ設定
# =============================
BASE_DIR = Path(__file__).resolve().parent.parent

# =============================
# セキュリティ設定
# =============================
SECRET_KEY = 'django-insecure-)l$5u7#*s5ls885avu*8rpmfiiiczle6vsr+y78%!cutwp%wpl'
DEBUG = True  # 本番では False にすること
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "gorozooo.com",
    "www.gorozooo.com",
    "192.168.1.16",
]

# =============================
# インストール済みアプリ
# =============================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'portfolio',
]

# =============================
# ミドルウェア
# =============================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# =============================
# URL・WSGI
# =============================
ROOT_URLCONF = 'stocksite.urls'
WSGI_APPLICATION = 'stocksite.wsgi.application'

# =============================
# テンプレート設定
# =============================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'portfolio.context_processors.ui',          # 任意のUI共通コンテキスト
                'portfolio.context_processors.bottom_tabs', # 下タブ用コンテキスト
            ],
        },
    },
]

# =============================
# データベース
# =============================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# =============================
# パスワードバリデーション
# =============================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =============================
# 言語・タイムゾーン
# =============================
LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True

# =============================
# 静的ファイル
# =============================
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"  # collectstatic 用（本番向け）

# =============================
# デフォルトフィールド
# =============================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================
# 認証設定
# =============================
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# =============================
# CSRF信頼設定（本番環境対応）
# =============================
CSRF_TRUSTED_ORIGINS = [
    "http://192.168.1.16:8000",
]

# =============================
# 本番運用時の追加設定例
# =============================
# SECURE_SSL_REDIRECT = True  # HTTPS強制リダイレクト
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# X_FRAME_OPTIONS = 'DENY'
