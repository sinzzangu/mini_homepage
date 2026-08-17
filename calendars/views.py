import hmac
import json
from datetime import datetime, time
from functools import wraps

from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from icalendar import Calendar, Event as ICalEvent

from .forms import SignupForm
from .models import Event, FeedToken

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 공개 회원가입이라 IP당 시도를 제한한다. 캐시는 LocMemCache(worker 1개)로 충분.
SIGNUP_MAX_ATTEMPTS = 5
SIGNUP_WINDOW_SECONDS = 3600


def require_xhr_header(view):
    """CSRF 토큰에 더한 2차 방어선.

    크로스사이트에서는 CORS preflight 없이 커스텀 헤더를 붙일 수 없다.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method in WRITE_METHODS and (
            request.headers.get("X-Requested-With") != "XMLHttpRequest"
        ):
            return JsonResponse(
                {"error": "X-Requested-With: XMLHttpRequest 헤더가 필요합니다."},
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapped


def client_ip(request):
    """Caddy 뒤에 있으므로 X-Forwarded-For의 첫 항목이 실제 클라이언트다."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def parse_moment(value):
    """ISO datetime 또는 날짜만 있는 문자열("2026-08-17")을 aware datetime으로."""
    if value in (None, ""):
        return None
    dt = parse_datetime(value)
    if dt is None:
        day = parse_date(value)
        if day is None:
            raise ValueError(f"날짜를 해석할 수 없습니다: {value!r}")
        dt = datetime.combine(day, time.min)
    if timezone.is_naive(dt):
        # 현재 타임존(Asia/Seoul) 기준으로 해석.
        dt = timezone.make_aware(dt)
    return dt


def serialize(event):
    """FullCalendar가 먹는 형태로 직렬화."""
    local_start = timezone.localtime(event.start)
    local_end = timezone.localtime(event.end) if event.end else None
    if event.all_day:
        # all-day는 날짜만 넘긴다(FullCalendar 관례: end는 exclusive).
        payload = {"start": local_start.date().isoformat()}
        if local_end:
            payload["end"] = local_end.date().isoformat()
    else:
        payload = {"start": local_start.isoformat()}
        if local_end:
            payload["end"] = local_end.isoformat()
    payload.update(id=event.id, title=event.title, allDay=event.all_day)
    extended = {}
    if event.description:
        extended["description"] = event.description
    if event.calendar_source_id:
        # 외부에서 가져온 일정은 소스 색으로 칠하고 드래그를 막는다(읽기 전용 미러).
        payload["backgroundColor"] = event.calendar_source.color
        payload["borderColor"] = event.calendar_source.color
        payload["editable"] = False
        extended["sourceName"] = event.calendar_source.name
    if extended:
        payload["extendedProps"] = extended
    return payload


def apply_payload(event, data, *, partial):
    """JSON body를 Event에 반영. partial=True면 들어온 키만 건드린다."""
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise ValueError("제목은 비워둘 수 없습니다.")
        event.title = title[:200]
    elif not partial:
        raise ValueError("title이 필요합니다.")

    if "allDay" in data or "all_day" in data:
        event.all_day = bool(data.get("allDay", data.get("all_day")))

    if "start" in data:
        start = parse_moment(data["start"])
        if start is None:
            raise ValueError("start가 필요합니다.")
        event.start = start
    elif not partial:
        raise ValueError("start가 필요합니다.")

    if "end" in data:
        event.end = parse_moment(data["end"])

    if "description" in data:
        event.description = data["description"] or ""

    if event.end and event.end < event.start:
        raise ValueError("종료가 시작보다 앞설 수 없습니다.")


def read_json(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 객체를 보내주세요.")
    return data


@login_required
@ensure_csrf_cookie
def calendar_page(request):
    return render(request, "calendars/calendar.html")


def signup(request):
    """공개 회원가입. 가입하면 자기 캘린더와 피드 토큰이 하나 생긴다."""
    if request.user.is_authenticated:
        return redirect("calendar")

    throttled = False
    form = SignupForm(request.POST if request.method == "POST" else None)

    if request.method == "POST":
        cache_key = f"signup-attempts:{client_ip(request)}"
        attempts = cache.get(cache_key, 0)
        if attempts >= SIGNUP_MAX_ATTEMPTS:
            throttled = True
        else:
            cache.set(cache_key, attempts + 1, SIGNUP_WINDOW_SECONDS)
            if form.is_valid():
                user = form.save()
                FeedToken.objects.create(user=user, label="기본")
                auth_login(request, user)
                return redirect("calendar")

    return render(
        request, "calendars/signup.html", {"form": form, "throttled": throttled}
    )


@login_required
@require_xhr_header
@require_http_methods(["GET", "POST"])
def events_collection(request):
    if request.method == "GET":
        events = Event.objects.filter(owner=request.user).select_related(
            "calendar_source"
        )
        try:
            window_start = parse_moment(request.GET.get("start"))
            window_end = parse_moment(request.GET.get("end"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if window_start:
            # 기간과 겹치는 것만. end가 없는 일정은 start 기준으로 판단.
            events = events.filter(
                Q(end__gt=window_start)
                | Q(end__isnull=True, start__gte=window_start)
            )
        if window_end:
            events = events.filter(start__lt=window_end)
        return JsonResponse([serialize(e) for e in events], safe=False)

    try:
        data = read_json(request)
        event = Event(owner=request.user)
        apply_payload(event, data, partial=False)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    event.save()
    return JsonResponse(serialize(event), status=201)


@login_required
@require_xhr_header
@require_http_methods(["GET", "PATCH", "DELETE"])
def event_detail(request, pk):
    # owner로 걸러서 조회한다. 남의 일정은 존재 자체를 알려주지 않고 404.
    try:
        event = Event.objects.select_related("calendar_source").get(
            pk=pk, owner=request.user
        )
    except Event.DoesNotExist:
        return JsonResponse({"error": "없는 일정입니다."}, status=404)

    if event.is_imported and request.method in ("PATCH", "DELETE"):
        # 미러이므로 여기서 고쳐도 다음 sync에 되돌아간다. 아예 막는다.
        return JsonResponse(
            {
                "error": f"'{event.calendar_source.name}'에서 가져온 일정입니다. "
                "원본 캘린더에서 수정하세요."
            },
            status=403,
        )

    if request.method == "DELETE":
        event.delete()
        return HttpResponse(status=204)

    if request.method == "GET":
        return JsonResponse(serialize(event))

    try:
        data = read_json(request)
        apply_payload(event, data, partial=True)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    event.save()
    return JsonResponse(serialize(event))


@require_http_methods(["GET"])
def feed(request):
    """구독용 ICS 피드 — 토큰이 사용자를 결정한다.

    구글/아이폰 구독 fetcher는 로그인을 못 하기 때문에 이 경로만 세션 인증
    밖에 있다. 대신 쿼리의 비밀 토큰이 비밀번호 역할을 하고, 그 토큰의
    주인 일정만 내려간다.
    """
    supplied = request.GET.get("token", "")
    owner = None
    if supplied:
        candidate = FeedToken.objects.filter(
            token=supplied, is_active=True
        ).select_related("user").first()
        # 타이밍 공격 방지용 상수시간 비교.
        if candidate and hmac.compare_digest(
            supplied.encode(), candidate.token.encode()
        ):
            owner = candidate.user
    if owner is None:
        return HttpResponse("Forbidden", status=403, content_type="text/plain")

    calendar = Calendar()
    calendar.add("prodid", "-//dodamthepig//calendar//KO")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", f"dodamthepig 캘린더 ({owner.username})")
    calendar.add("x-wr-timezone", "Asia/Seoul")

    stamp = timezone.now()
    # source="local"만 내보낸다. Phase 3에서 가져올 외부 일정을 되돌려
    # 내보내면 구독 루프가 생기므로 미리 차단.
    for event in Event.objects.filter(owner=owner, source="local"):
        entry = ICalEvent()
        entry.add("uid", f"{event.id}@dodamthepig.duckdns.org")
        entry.add("dtstamp", stamp)
        entry.add("summary", event.title)
        if event.all_day:
            # DATE 타입(시간 없음). 이걸 틀리면 아이폰이 시간 지정 일정으로 표시한다.
            entry.add("dtstart", timezone.localtime(event.start).date())
            if event.end:
                entry.add("dtend", timezone.localtime(event.end).date())
        else:
            entry.add("dtstart", timezone.localtime(event.start))
            if event.end:
                entry.add("dtend", timezone.localtime(event.end))
        if event.description:
            entry.add("description", event.description)
        calendar.add_component(entry)

    return HttpResponse(
        calendar.to_ical(), content_type="text/calendar; charset=utf-8"
    )
