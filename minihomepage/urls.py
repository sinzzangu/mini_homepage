from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def hello(request):
    return HttpResponse(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<title>dodamthepig</title></head>"
        "<body style='font-family:system-ui;display:grid;place-items:center;"
        "min-height:100vh;margin:0;background:#1a1a2e;color:#eee'>"
        "<main style='text-align:center'>"
        "<h1>Hello, World! 🐷</h1>"
        "<p style='color:#9ca3af'>Django on Oracle Free Tier</p>"
        "</main></body></html>"
    )


urlpatterns = [
    path("", hello),
    path("admin/", admin.site.urls),
    path("cal/", include("calendars.urls")),
]
