import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DEBUG = os.environ.get("DJANGO_DEBUG", "") == "1"


def _domains(value, name):
    """쉼표로 구분된 도메인 문자열을 `(호스트, 포트)` 목록으로 정규화한다.

    소문자로 낮추고 순서를 유지한 채 중복만 없앤다. 호스트는 포트를 뗀 형태로
    돌려준다 — `calendars/middleware.py`가 Host를 같은 규칙(소문자·포트 제거)으로
    비교하므로 여기서 미리 맞춰 둔다. 포트는 CSRF 오리진에만 필요하므로 따로 남긴다.

    스킴(`https://`)이나 경로가 섞인 값은 조용히 넘기지 않고 부팅을 실패시킨다.
    그대로 두면 엉뚱한 호스트 목록이 만들어져 모든 요청이 400이 되고,
    로그에 원인이 남지 않아 찾기 어렵다.
    """
    entries = []
    for raw in value.split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        if "/" in entry:
            raise ImproperlyConfigured(
                f"{name}: 호스트명만 적어야 한다(스킴·경로 금지). "
                f"잘못된 값: {entry!r} — 예: 'example.com' 또는 'example.com:8443'"
            )
        host, _, port = entry.partition(":")
        if not host or (port and not port.isdigit()):
            raise ImproperlyConfigured(
                f"{name}: 호스트[:포트] 형식이 아니다. 잘못된 값: {entry!r}"
            )
        if (host, port) not in entries:
            entries.append((host, port))
    return entries


# 도메인은 공개 저장소에 박아 두지 않고 환경변수로 받는다.
# 기본값이 현재 운영 도메인이므로 .env에 아무것도 넣지 않으면 지금과 동작이 같다.
# (빈 값도 기본값으로 떨어진다 — .env를 채우다 비워 둬도 서비스가 죽지 않게.)
_site_domains = _domains(os.environ.get("SITE_DOMAIN", ""), "SITE_DOMAIN") or [
    ("dodamthepig.duckdns.org", "")
]
SITE_DOMAIN = _site_domains[0][0]
# 캘린더 서브도메인. 비워 두면 cal.<SITE_DOMAIN>(포트도 메인과 같게).
_calendar_domains = _domains(os.environ.get("CALENDAR_DOMAIN", ""), "CALENDAR_DOMAIN") or [
    (f"cal.{SITE_DOMAIN}", _site_domains[0][1])
]
CALENDAR_DOMAIN = _calendar_domains[0][0]
# 도메인을 더 붙일 때 쓴다(쉼표 구분). ALLOWED_HOSTS와 CSRF 신뢰 오리진에 함께 들어간다.
EXTRA_ALLOWED_HOSTS = os.environ.get("EXTRA_ALLOWED_HOSTS", "")

# 바깥에서 접속하는 도메인들. 여기서 ALLOWED_HOSTS·CSRF 오리진이 파생된다.
_public_domains = []
for _entry in (
    _site_domains + _calendar_domains + _domains(EXTRA_ALLOWED_HOSTS, "EXTRA_ALLOWED_HOSTS")
):
    if _entry not in _public_domains:
        _public_domains.append(_entry)

# ALLOWED_HOSTS 비교는 Django가 포트를 뗀 뒤에 하므로 호스트만 넣는다.
_public_hosts = []
for _host, _port in _public_domains:
    if _host not in _public_hosts:
        _public_hosts.append(_host)

# localhost·127.0.0.1은 컨테이너 안에서 도는 내부 검증(curl, healthcheck)에 쓰므로 항상 남긴다.
ALLOWED_HOSTS = _public_hosts + [h for h in ("localhost", "127.0.0.1") if h not in _public_hosts]
# CSRF 오리진은 포트까지 정확히 맞아야 하므로 포트가 지정된 도메인은 붙여서 넣는다.
# 로컬 주소는 평문 HTTP라 https 오리진으로 넣을 의미가 없다.
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}:{port}" if port else f"https://{host}" for host, port in _public_domains
]

# cal 서브도메인은 캘린더를 루트에 올린 별도 URLconf를 쓴다.
# (Caddy에서 /cal 프리픽스를 rewrite하면 로그인 리다이렉트가 깨진다.)
# 미들웨어가 포트를 뗀 소문자 Host와 비교하므로 여기도 호스트만 담는다.
CALENDAR_HOSTS = {host for host, _ in _calendar_domains}

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
