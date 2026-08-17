"""cal.dodamthepig.duckdns.org 전용 URLconf — 캘린더가 루트에 온다.

admin은 여기 없다. 메인 도메인에서만 접근한다.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("calendars.urls")),
]
