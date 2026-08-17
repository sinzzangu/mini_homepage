import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DEBUG = os.environ.get("DJANGO_DEBUG", "") == "1"

MAIN_HOST = "dodamthepig.duckdns.org"
CALENDAR_HOST = "cal.dodamthepig.duckdns.org"

ALLOWED_HOSTS = [MAIN_HOST, CALENDAR_HOST, "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = [f"https://{MAIN_HOST}", f"https://{CALENDAR_HOST}"]

# cal 서브도메인은 캘린더를 루트에 올린 별도 URLconf를 쓴다.
# (Caddy에서 /cal 프리픽스를 rewrite하면 로그인 리다이렉트가 깨진다.)
CALENDAR_HOSTS = {CALENDAR_HOST}

# Caddy terminates TLS and proxies plain HTTP to us.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "calendars",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # URLconf 선택은 URL 해석보다 먼저 끝나야 하므로 CommonMiddleware 앞에 둔다.
    "calendars.middleware.HostBasedURLConfMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # admin·로그인 페이지가 공개되어 있으므로 클릭재킹 방어를 켠다.
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "minihomepage.urls"
WSGI_APPLICATION = "minihomepage.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# PostgreSQL. 컨테이너 이름 db 로 내부 네트워크에서만 접근한다.
# 데이터는 named volume(pgdata)에 있으므로 컨테이너를 지워도 남는다.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # gunicorn worker 1 + threads 4 이므로 커넥션을 재사용한다.
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# URL 이름으로 둔다. Host별 URLconf에서 각각 알맞은 경로로 reverse된다.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "calendar"
LOGOUT_REDIRECT_URL = "login"

# TLS는 Caddy가 담당하고 평문은 내부망에만 존재하므로 쿠키는 secure로 고정.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # manifest 없는 압축 저장소. 파일 누락 시 500이 나지 않는다.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
