from django.contrib import admin

from .models import CalendarSource, Event, FeedToken


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "start", "end", "all_day", "source")
    list_filter = ("owner", "all_day", "source", "calendar_source")
    search_fields = ("title", "description")
    date_hierarchy = "start"


@admin.register(CalendarSource)
class CalendarSourceAdmin(admin.ModelAdmin):
    # URL은 비밀번호급이라 목록에 넣지 않는다(상세 화면에서만 보인다).
    list_display = (
        "name",
        "owner",
        "color",
        "is_active",
        "last_synced_at",
        "failure_count",
        "last_status",
    )
    list_filter = ("owner", "is_active")
    readonly_fields = (
        "last_synced_at",
        "last_status",
        "failure_count",
        "next_attempt_at",
        "created_at",
    )


@admin.register(FeedToken)
class FeedTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "label", "is_active", "created_at")
    list_filter = ("is_active", "user")
    # 토큰 값은 비밀번호급이므로 목록에 노출하지 않는다.
    readonly_fields = ("token", "created_at")
