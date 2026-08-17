from django.conf import settings


class HostBasedURLConfMiddleware:
    """cal 서브도메인에는 캘린더를 루트에 올린 URLconf를 쓴다.

    Caddy에서 `/X` → `/cal/X` 로 rewrite하는 방식은 로그인처럼 리다이렉트를
    쓰는 흐름에서 깨진다(Django가 `/cal/login/`을 반환하면 서브도메인에서는
    `/cal/cal/login/`이 된다). Host를 보고 URLconf를 바꾸면 reverse()가
    도메인별로 올바른 경로를 만들어 준다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().partition(":")[0].lower()
        if host in settings.CALENDAR_HOSTS:
            request.urlconf = "minihomepage.urls_cal"
        return self.get_response(request)
