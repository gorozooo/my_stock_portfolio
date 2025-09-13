from pathlib import Path
import os

# =============================
# 基本
# =============================
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-)l$5u7#*s5ls885avu*8rpmfiiiczle6vsr+y78%!cutwp%wpl'
#DEBUG = False  # 本番運用前提（必要なら True に）
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "gorozooo.com", "www.gorozooo.com", "192.168.1.16"]

# =============================
# アプリ
# =============================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'portfolio',
    'django.contrib.humanize',
    # ← 追加：毎日16:00の自動実行に使う
    'django_crontab',
]

# =============================
# ミドルウェア
# =============================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware",  # ← WhiteNoiseはSecurityの直後が推奨
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'stocksite.urls'

# =============================
# テンプレート
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
                'portfolio.context_processors.ui',
                'portfolio.context_processors.bottom_tabs',
            ],
        },
    },
]

WSGI_APPLICATION = 'stocksite.wsgi.application'

# =============================
# DB
# =============================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# =============================
# 認証
# =============================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =============================
# ロケール / タイムゾーン
# =============================
LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True  # DBはUTC、表示は TIME_ZONE（Asia/Tokyo）

# =============================
# 静的ファイル
# =============================
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]          # 開発: /static/ 配下
STATIC_ROOT = BASE_DIR / "staticfiles"            # 本番: collectstatic の出力先
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# =============================
# 認証系URL
# =============================
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# =============================
# セキュリティ
# =============================
CSRF_TRUSTED_ORIGINS = [
    "http://192.168.1.16:8000",
    "https://gorozooo.com",
    "https://www.gorozooo.com",
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================
# Sentry
# =============================
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

sentry_sdk.init(
    dsn="https://2748d2771c790b721f21334f9a4d8a01@o4509931851481088.ingest.us.sentry.io/4509932914999296",
    integrations=[DjangoIntegration()],
    traces_sample_rate=1.0,
    send_default_pii=True,
)

# =============================
# CRON（毎日16:00 JSTにスナップショット）
# =============================
# サーバーOSのcronが参照するタイムゾーンに依存します。
#  - サーバーTZが JST の場合 → そのまま 16:00 でOK
#  - サーバーTZが UTC の場合 → 7:00(UTC) が JST 16:00 相当
#
# まずは下記の 16:00 を使い、ずれていたら 7:00 に変更してください。
CRONJOBS = [
    # JSTサーバーの場合（毎日16:00）
    ('0 16 * * *', 'django.core.management.call_command', ['snapshot_assets']),
    ('0 16 * * *', 'django.core.management.call_command', ['snapshot_metrics']),
    ("0 16 * * *", "django.core.management.call_command", ["snapshot_today_pnl"], {"verbosity": 1}),
    # もしサーバーTZがUTCなら、上をコメントアウトして下を有効化
    # ('0 7 * * *', 'django.core.management.call_command', ['snapshot_assets']),
]

# === Bench & Sector targets ===
BENCH_TICKERS = {
    "TOPIX": ["1306.T", "1308.T"],   # TOPIX ETF（野村 / iShares）
    "NIKKEI": ["1321.T", "^N225"],   # 日経225 ETF → 取れなければ指数
}
# 目標配分（%）；お好みで
SECTOR_TARGETS = {
    "銀行": 10, "保険": 8, "商社": 10, "小売": 8, "不動産": 8,
    "機械": 12, "化学": 10, "電機": 12, "自動車": 12, "その他": 10,
}
SECTOR_TARGETS_DEFAULT = 100  # 合計100%になる前提（ズレても自動正規化）

# =============================
# 本番向けオプション例（必要に応じて）
# =============================
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# X_FRAME_OPTIONS = 'DENY'
