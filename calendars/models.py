import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .ics_fetch import SourceError, normalize_url

MAX_SOURCES_PER_USER = 5

hex_color_validator = RegexValidator(
    r"^#[0-9a-fA-F]{6}$", "#rrggbb 형식이어야 합니다 (예: #3b82f6)."
)


def generate_feed_token():
    """64자 hex. 이 값이 곧 피드의 비밀번호다."""
    return secrets.token_hex(32)


class CalendarSource(models.Model):
    """가져올 외부 ICS 피드 하나 (구글 비공개 주소, 아이클라우드 공유 링크 등).

    URL은 비밀번호급이다 — 목록 화면에 노출하지 않는다.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_sources",
    )
    name = models.CharField(max_length=50)
    # URLField를 쓰지 않는 이유: 기본 URLValidator가 webcal:// 스킴을 먼저 거부해서
    # 아이클라우드 구독 링크를 붙여넣을 수 없다. 검증은 normalize_url이 담당한다.
    url = models.CharField(max_length=500)
    color = models.CharField(max_length=7, default="#3b82f6", validators=[hex_color_validator])
    is_active = models.BooleanField(default=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=200, blank=True)
    # 연속 실패 횟수. 백오프 계산에 쓰이고 성공하면 0으로 돌아간다.
    failure_count = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.owner})"

    def _normalize_url(self):
        try:
            self.url = normalize_url(self.url)
        except SourceError as exc:
            raise ValidationError({"url": str(exc)}) from exc

    def clean_fields(self, exclude=None):
        # 필드 검증보다 먼저 정규화해야 webcal:// 링크를 그대로 붙여넣을 수 있다.
        if self.url:
            self._normalize_url()
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        # full_clean()을 거치지 않는 경로(직접 create 등)에서도 스킴을 강제한다.
        self._normalize_url()
        return super().save(*args, **kwargs)

    def clean(self):
        # 등록 시점 검사. fetch 시점에도 같은 검사를 다시 한다.
        if self.owner_id and self._state.adding:
            existing = CalendarSource.objects.filter(owner_id=self.owner_id).count()
            if existing >= MAX_SOURCES_PER_USER:
                raise ValidationError(
                    f"소스는 사용자당 {MAX_SOURCES_PER_USER}개까지만 등록할 수 있습니다."
                )


class Event(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="events",
    )
    title = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    # "local" = 여기서 만든 일정, "ics" = 외부에서 가져온 일정.
    # 피드는 "local"만 내보낸다 (가져온 일정을 되돌려 내보내면 구독 루프).
    source = models.CharField(max_length=50, default="local")

    # 외부에서 가져온 일정만 채워진다. 이 두 값이 upsert 키다.
    calendar_source = models.ForeignKey(
        CalendarSource,
        on_delete=models.CASCADE,
        related_name="events",
        null=True,
        blank=True,
    )
    external_uid = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["start"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar_source", "external_uid"],
                condition=models.Q(calendar_source__isnull=False),
                name="unique_external_event_per_source",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.start:%Y-%m-%d %H:%M})"

    @property
    def is_imported(self):
        return self.calendar_source_id is not None


class FeedToken(models.Model):
    """사용자별 ICS 구독 토큰.

    한 사용자가 여러 개를 가질 수 있다(기기별로 나눠 주고 개별 폐기 가능).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_tokens",
    )
    token = models.CharField(
        max_length=64, unique=True, default=generate_feed_token, db_index=True
    )
    label = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        state = "활성" if self.is_active else "폐기"
        return f"{self.user}: {self.label or '무제'} ({state})"
