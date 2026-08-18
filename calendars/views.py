import hmac
import json
from datetime import datetime, time
from functools import wraps
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from icalendar import Calendar, Event as ICalEvent

from .forms import CalendarSourceForm, SignupForm
from .models import MAX_SOURCES_PER_USER, CalendarSource, Event, FeedToken

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 공개 회원가입이라 IP당 시도를 제한한다. 캐시는 LocMemCache(worker 1개)로 충분.
SIGNUP_MAX_ATTEMPTS = 5
SIGNUP_WINDOW_SECONDS = 3600

# 재발급이 폐기 토큰을 무한히 쌓으면 설정 페이지 조회만으로 1 GB 박스의 worker가
# 죽는다. 그래서 (1) 사용자별 폐기 토큰은 최근 N개만 남기고 지우고,
# (2) 재발급 자체도 사용자당 시간당 횟수를 제한한다.
MAX_REVOKED_TOKENS_KEPT = 5
TOKEN_ROTATE_MAX_ATTEMPTS = 10
TOKEN_ROTATE_WINDOW_SECONDS = 3600


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


def feed_url(request, token):
    """구독용 절대 URL을 만든다.

    reverse("feed")는 요청에 걸린 Host별 URLconf로 풀리므로 메인 도메인에서는
    /cal/feed.ics, 캘린더 서브도메인에서는 /feed.ics가 나온다.
    """
    base = request.build_absolute_uri(reverse("feed"))
    return f"{base}?{urlencode({'token': token})}"


def mask_source_url(url):
    """소스 URL도 비밀번호급이라 화면에는 호스트까지만 보여준다.

    비밀은 경로·쿼리에 들어 있고(구글의 비공개 주소, 아이클라우드 토큰),
    호스트는 어느 서비스에서 가져오는 소스인지 구분하는 데만 필요하다.
    """
    parts = urlsplit(url)
    host = parts.hostname or "?"
    if parts.query or parts.path not in ("", "/"):
        return f"{host}/…"
    return host


def posted_id(request, field="id"):
    """POST로 온 pk를 정수로. 이상한 값은 남의 것과 똑같이 404로 막는다."""
    try:
        return int(request.POST.get(field, ""))
    except (TypeError, ValueError) as exc:
        raise Http404("잘못된 id입니다.") from exc


def prune_revoked_tokens(user):
    """폐기 토큰은 최근 MAX_REVOKED_TOKENS_KEPT개만 남기고 지운다.

    폐기분은 되살릴 수 없고 화면에도 개수만 뜨므로 보관할 이유가 없다.
    남겨 두면 재발급을 반복할 때 행이 무한히 쌓여 설정 페이지 조회가
    worker(1개)를 압박한다. 지운 개수를 돌려준다.
    """
    revoked = FeedToken.objects.filter(user=user, is_active=False)
    # created_at은 초 단위로 겹칠 수 있어 id로 tie-break한다.
    keep_ids = list(
        revoked.order_by("-created_at", "-id").values_list("id", flat=True)[
            :MAX_REVOKED_TOKENS_KEPT
        ]
    )
    removed, _ = revoked.exclude(id__in=keep_ids).delete()
    return removed


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def settings_page(request):
    """설정 화면 — 내 피드 주소 확인과 외부 캘린더 소스 관리.

    쓰기는 모두 POST + CSRF 토큰(표준 폼 방식)이고, 조회·수정은 전부
    request.user로 스코핑한다. 남의 토큰·소스 id를 넣으면 404다.
    토큰 값은 화면에만 보여주고 메시지·로그에는 남기지 않는다.
    이 HTML에는 피드 토큰 전문이 들어가므로 @never_cache로 브라우저
    디스크 캐시에 남지 않게 한다.
    """
    source_form = None

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "token-rotate":
            # 재발급은 행을 만드는 동작이라 사용자당 횟수를 제한한다.
            rotate_key = f"token-rotate:{request.user.pk}"
            rotations = cache.get(rotate_key, 0)
            if rotations >= TOKEN_ROTATE_MAX_ATTEMPTS:
                messages.error(
                    request,
                    "재발급을 너무 자주 했습니다. 한 시간 뒤에 다시 시도해 주세요.",
                )
                return redirect("settings")
            cache.set(rotate_key, rotations + 1, TOKEN_ROTATE_WINDOW_SECONDS)

            label = (request.POST.get("label") or "").strip()[:50] or "기본"
            with transaction.atomic():
                # 재발급 = 기존 활성 토큰을 모두 끊고 새로 하나 만든다.
                FeedToken.objects.filter(user=request.user, is_active=True).update(
                    is_active=False
                )
                FeedToken.objects.create(user=request.user, label=label)
                # 방금 늘어난 폐기분까지 포함해 오래된 것은 정리한다.
                prune_revoked_tokens(request.user)
            messages.success(
                request,
                "새 피드 주소를 발급했습니다. 기존 주소는 폐기됐으니 "
                "구독 중인 앱에 새 주소를 다시 넣어 주세요.",
            )
            return redirect("settings")

        if action == "token-revoke":
            token = get_object_or_404(
                FeedToken, pk=posted_id(request), user=request.user
            )
            label = token.label or "무제"
            with transaction.atomic():
                token.is_active = False
                token.save(update_fields=["is_active"])
                prune_revoked_tokens(request.user)
            messages.success(request, f"'{label}' 주소를 폐기했습니다.")
            return redirect("settings")

        if action == "source-add":
            # instance에 owner를 미리 채운다 — 모델 clean()의 5개 제한이
            # owner를 봐야 동작한다.
            source_form = CalendarSourceForm(
                request.POST, instance=CalendarSource(owner=request.user)
            )
            # 5개 제한은 clean()의 count()로 검사하므로 threads=4에서 동시에 들어온
            # POST 두 개가 둘 다 통과할 수 있다. 소유자 행을 잠가 같은 사용자의
            # 소스 추가를 직렬화한다(검증과 INSERT가 한 트랜잭션 안이어야 한다).
            with transaction.atomic():
                get_user_model().objects.select_for_update().filter(
                    pk=request.user.pk
                ).first()
                if source_form.is_valid():
                    # ModelForm이 full_clean()을 거치므로 normalize_url(https 강제,
                    # webcal 변환)과 개수 제한이 이미 적용된 상태다.
                    source = source_form.save()
                    messages.success(
                        request,
                        f"'{source.name}'을 추가했습니다. 다음 동기화(15분 주기)에 "
                        "일정이 들어옵니다.",
                    )
                    return redirect("settings")
            # 폼 오류는 리다이렉트하면 사라지므로 입력값을 채운 채로 다시 그린다.

        elif action == "source-toggle":
            source = get_object_or_404(
                CalendarSource, pk=posted_id(request), owner=request.user
            )
            with transaction.atomic():
                source.is_active = not source.is_active
                source.save(update_fields=["is_active"])
                if source.is_active:
                    messages.success(
                        request,
                        f"'{source.name}' 동기화를 다시 켰습니다. 다음 동기화"
                        "(15분 주기)에 일정이 다시 들어옵니다.",
                    )
                else:
                    # 끄면 sync 대상에서 빠져 미러 청소가 다시 돌지 않는다. 그대로
                    # 두면 가져온 일정이 영구히 남고 UI에서는 지울 수도 없으므로
                    # (is_imported → PATCH/DELETE 403) 여기서 함께 지운다.
                    # 다시 켜면 다음 sync에 복원된다.
                    removed, _ = Event.objects.filter(
                        calendar_source=source, owner=request.user
                    ).delete()
                    messages.success(
                        request,
                        f"'{source.name}' 동기화를 껐습니다. 이 소스에서 가져온 "
                        f"일정 {removed}개도 함께 지웠습니다(다시 켜면 복원됩니다).",
                    )
            return redirect("settings")

        elif action == "source-delete":
            source = get_object_or_404(
                CalendarSource, pk=posted_id(request), owner=request.user
            )
            name = source.name
            # 이 소스에서 가져온 일정도 FK CASCADE로 함께 사라진다.
            source.delete()
            messages.success(
                request, f"'{name}'과 그 소스에서 가져온 일정을 삭제했습니다."
            )
            return redirect("settings")

        else:
            raise Http404("알 수 없는 요청입니다.")

    # 활성 토큰만 객체로 올린다. 폐기분은 개수만 필요하므로 count()로 센다
    # (전부 파이썬 객체로 올리면 worker 1개짜리 서버에서 메모리가 위험하다).
    active_tokens = FeedToken.objects.filter(user=request.user, is_active=True)
    revoked_count = FeedToken.objects.filter(
        user=request.user, is_active=False
    ).count()
    sources = list(CalendarSource.objects.filter(owner=request.user))
    for source in sources:
        source.masked_url = mask_source_url(source.url)

    return render(
        request,
        "calendars/settings.html",
        {
            "active_tokens": [
                {
                    "id": token.id,
                    "label": token.label,
                    "created_at": token.created_at,
                    "url": feed_url(request, token.token),
                }
                for token in active_tokens
            ],
            "revoked_count": revoked_count,
            "sources": sources,
            "source_form": source_form or CalendarSourceForm(),
            "source_limit": MAX_SOURCES_PER_USER,
            "source_slots_left": MAX_SOURCES_PER_USER - len(sources),
        },
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
