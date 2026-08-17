"""외부 ICS 소스를 읽어 Event로 upsert한다.

가져온 일정은 읽기 전용 미러다. 웹에서 수정하지 않고, 다음 sync에서 원본 상태로
맞춘다. 피드로 다시 내보내지도 않는다(source="ics" → 피드는 "local"만 발행).
"""

from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone
from icalendar import Calendar

from .ics_fetch import SourceError, fetch_ics
from .models import Event

# 과거 일정을 무한히 가져오지 않는다. 1 GB 서버라 DB를 작게 유지한다.
IMPORT_PAST_DAYS = 90
# 연속 실패 백오프: 15분 → 30 → 60 → … 최대 6시간
BACKOFF_BASE_MINUTES = 15
BACKOFF_MAX_MINUTES = 360


def _aware(value):
    """ICS의 date/naive datetime/aware datetime을 aware datetime으로 맞춘다."""
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            # 타임존 없는 floating time은 현재 타임존(Asia/Seoul)으로 해석한다.
            return timezone.make_aware(value)
        return value
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time.min))
    return None


def _event_fields(component):
    """VEVENT 하나를 Event 필드 dict로. 해석 불가면 None."""
    try:
        raw_start = component.decoded("DTSTART")
    except (KeyError, ValueError):
        return None

    start = _aware(raw_start)
    if start is None:
        return None

    # DTSTART가 DATE 타입이면 종일 일정이다.
    all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)

    end = None
    if "DTEND" in component:
        try:
            end = _aware(component.decoded("DTEND"))
        except (KeyError, ValueError):
            end = None
    elif "DURATION" in component:
        try:
            duration = component.decoded("DURATION")
            if isinstance(duration, timedelta):
                end = start + duration
        except (KeyError, ValueError):
            end = None

    if end and end < start:
        end = None

    title = str(component.get("SUMMARY") or "(제목 없음)").strip()[:200]
    description = str(component.get("DESCRIPTION") or "").strip()

    return {
        "title": title or "(제목 없음)",
        "start": start,
        "end": end,
        "all_day": all_day,
        "description": description,
    }


@transaction.atomic
def sync_source(source, *, now=None):
    """소스 하나를 동기화하고 통계 dict를 돌려준다. 실패는 SourceError."""
    now = now or timezone.now()
    raw = fetch_ics(source.url)

    try:
        calendar = Calendar.from_ical(raw)
    except Exception as exc:  # icalendar는 다양한 예외를 던진다
        raise SourceError(f"ICS 파싱 실패: {exc}") from exc

    floor = now - timedelta(days=IMPORT_PAST_DAYS)
    seen_uids = set()
    created = updated = skipped_old = skipped_bad = recurring = 0

    for component in calendar.walk("VEVENT"):
        uid = str(component.get("UID") or "").strip()
        if not uid:
            skipped_bad += 1
            continue
        fields = _event_fields(component)
        if fields is None:
            skipped_bad += 1
            continue
        # 반복 일정은 아직 펼치지 않는다(Phase 4). 원본 일정만 하나 들어간다.
        if component.get("RRULE"):
            recurring += 1
        reference = fields["end"] or fields["start"]
        if reference < floor:
            skipped_old += 1
            continue

        _obj, was_created = Event.objects.update_or_create(
            calendar_source=source,
            external_uid=uid[:255],
            defaults={**fields, "owner": source.owner, "source": "ics"},
        )
        seen_uids.add(uid[:255])
        if was_created:
            created += 1
        else:
            updated += 1

    # 원본에서 사라진 일정은 여기서도 지운다(미러이므로).
    removed, _ = (
        Event.objects.filter(calendar_source=source)
        .exclude(external_uid__in=seen_uids)
        .delete()
    )

    source.last_synced_at = now
    source.failure_count = 0
    source.next_attempt_at = None
    status = f"성공: 신규 {created}, 갱신 {updated}, 삭제 {removed}"
    if skipped_old:
        status += f", 오래된 일정 제외 {skipped_old}"
    if recurring:
        status += f", 반복 일정 {recurring}건은 펼치지 않음"
    if skipped_bad:
        status += f", 해석 실패 {skipped_bad}"
    source.last_status = status[:200]
    source.save(
        update_fields=[
            "last_synced_at",
            "failure_count",
            "next_attempt_at",
            "last_status",
        ]
    )

    return {
        "created": created,
        "updated": updated,
        "removed": removed,
        "skipped_old": skipped_old,
        "skipped_bad": skipped_bad,
        "recurring": recurring,
        "status": status,
    }


def record_failure(source, message, *, now=None):
    """실패를 기록하고 다음 시도 시각을 뒤로 미룬다(지수 백오프)."""
    now = now or timezone.now()
    source.failure_count += 1
    delay = min(
        BACKOFF_BASE_MINUTES * (2 ** (source.failure_count - 1)),
        BACKOFF_MAX_MINUTES,
    )
    source.next_attempt_at = now + timedelta(minutes=delay)
    source.last_status = f"실패({source.failure_count}회): {message}"[:200]
    source.save(
        update_fields=["failure_count", "next_attempt_at", "last_status"]
    )
    return delay
