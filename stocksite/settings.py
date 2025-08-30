from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-)l$5u7#*s5ls885avu*8rpmfiiiczle6vsr+y78%!cutwp%wpl'

DEBUG = False

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "gorozooo.com", "www.gorozooo.com", "192.168.1.16"]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'portfolio',
    'django.contrib.humanize',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'stocksite.urls'

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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ja'
TIME_ZONE = 'Asia/Tokyo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

CSRF_TRUSTED_ORIGINS = ["http://192.168.1.16:8000""https://gorozooo.com","https://www.gorozooo.com",]

# StockMaster Excel 保存先
STOCKMASTER_XLSX_PATH = Path(BASE_DIR) / "data" / "StockMaster_latest.xlsx"

# キャッシュバスティングのために推奨
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# 本番用静的ファイル出力先
STATIC_ROOT = BASE_DIR / "staticfiles"

# 本番用の設定（DEBUG = False のままでもOK）
import logging.config

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "slack": {
            "level": "ERROR",
            "class": "logging.handlers.HTTPHandler",
            "host": "hooks.slack.com",
            "url": "/services/XXXXXXXXX/XXXXXXXXX/XXXXXXXXXXXXXXXXXXXX",
            "method": "POST",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["slack"],
            "level": "ERROR",
            "propagate": True,
        },
    },
}

# =============================
# 本番運用時の追加設定例
# =============================
# SECURE_SSL_REDIRECT = True  # HTTPS強制リダイレクト
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# X_FRAME_OPTIONS = 'DENY'
