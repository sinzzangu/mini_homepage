# CLAUDE.md — minihomepage

이 저장소는 Django 앱 코드만 담는다. 배포 인프라(compose, Caddyfile, .env)는
서버의 스택 디렉터리에 있고 커밋되지 않는다 — 서버에서 작업할 때는 상위
`../CLAUDE.md`도 함께 읽을 것.

앱을 더 붙일 계획이므로 **캘린더는 하나의 기능**이다. 캘린더 전용 코드를
`minihomepage/`(설정 패키지) 쪽으로 새지 않게 할 것.

## 이름 규칙

- `minihomepage/` — 설정 패키지. `DJANGO_SETTINGS_MODULE=minihomepage.settings`
- `calendars/` — 캘린더 기능 앱. **`calendar`로 이름을 바꾸지 말 것**:
  파이썬 표준 모듈 `calendar`를 가려서 Django(`django/utils/dateformat.py`)가
  임포트에 실패하고 앱이 부팅부터 깨진다
- DB 테이블은 `calendars_event` 등. 앱 이름을 또 바꾸려면 테이블명,
  `django_migrations.app`, `django_content_type.app_label`을 함께 옮겨야 한다

## URL — Host별로 URLconf가 다르다

`calendars/middleware.py`의 `HostBasedURLConfMiddleware`가 Host를 보고
`minihomepage.urls`(메인) 또는 `minihomepage.urls_cal`(캘린더 서브도메인)을 고른다.

**Caddy rewrite로 `/cal` 프리픽스를 붙이는 방식으로 되돌리지 말 것.** Django가
내보내는 리다이렉트(`/cal/login/`)가 서브도메인에서 `/cal/cal/login/`이 되어
로그인이 불가능해진다. 같은 이유로 `LOGIN_URL` 등은 경로가 아니라 **URL 이름**이어야
도메인별로 올바르게 reverse된다.

프론트 JS는 API를 **상대경로**(`api/events`)로 부른다 — 같은 템플릿이 양쪽 도메인에서
돌기 때문. 절대경로로 바꾸면 한쪽이 깨진다.

## 인증·권한

- Django 세션 로그인. 회원가입은 공개이고 IP당 시간당 5회 제한
  (`calendars/views.py`의 `SIGNUP_MAX_ATTEMPTS`, LocMemCache 사용 — worker 1개 전제)
- 가입하면 자기 캘린더 + `FeedToken` 1개가 자동 생성된다
- 쓰기 API는 Django CSRF(`X-CSRFToken`) + `X-Requested-With` 2중 검사
- 세션·CSRF 쿠키는 `Secure` 고정 → **평문 HTTP로는 로그인 테스트가 안 된다.**
  세션이 필요한 검증은 HTTPS로 (curl은 `-c/-b` 쿠키 자 사용)
- admin은 메인 도메인 URLconf에만 있고 `is_staff`가 필요하다

## 데이터 모델 (`calendars/models.py`)

- `Event` — `owner`(필수), title, start, end(nullable), all_day, description, source
  - 모든 조회·수정이 `owner=request.user`로 걸린다. 남의 일정은 404로 숨긴다
  - `source`: `"local"`(직접 만든 것) / `"ics"`(가져온 것)
  - `calendar_source`(FK, nullable) + `external_uid` — 가져온 일정만 채워지고
    이 둘이 upsert 키(부분 unique 제약). 가져온 일정은 PATCH·DELETE가 403
- `CalendarSource` — 외부 ICS 피드. **`url`은 비밀번호급**이라 admin 목록에 노출하지 않는다.
  사용자당 5개 제한(`MAX_SOURCES_PER_USER`)
  - `url`이 `URLField`가 아닌 이유: 기본 URLValidator가 `webcal://`을 먼저 거부해서
    아이클라우드 링크를 붙여넣을 수 없다. 검증은 `normalize_url`이 한다
- `FeedToken` — 사용자별 구독 토큰(64자 hex). 여러 개 발급·개별 폐기 가능.
  폐기 토큰은 최근 5개만 남기고 정리된다(무제한 누적 시 설정 페이지가 worker를 죽인다)
  - 피드는 `source="local"`만 내보낸다. 가져온 일정을 되돌려 내보내면 구독 루프가 생긴다

## `calendars/ics_fetch.py` — SSRF 방어가 이 파일의 존재 이유다

사용자가 임의 URL을 등록하는 기능이라, 서버가 사설망이나 클라우드
메타데이터(169.254.169.254)로 요청하게 만드는 공격이 가능하다.
**아래 방어를 약화시키지 말 것:**

- https만 허용. `webcal://`은 등록 시 https로 정규화
- 호스트를 직접 DNS 조회해 **응답된 모든 IP**를 검사(라운드로빈에 사설 IP 섞기 차단)
- 검사한 IP로 직접 연결하고 인증서·SNI만 원래 호스트명으로 검증 → **DNS 리바인딩 차단**.
  `urllib`/`requests`로 바꾸면 라이브러리가 DNS를 다시 조회해서 이 보호가 사라진다
- 리다이렉트를 자동으로 따라가지 않고 hop마다 같은 검사를 반복(최대 3회)
- 응답 5 MB / 타임아웃 10초, `BEGIN:VCALENDAR`로 시작하는지 확인
- 실패한 소스는 지수 백오프(15분 → 최대 6시간), `next_attempt_at`으로 관리

## 그 밖의 함정

- `python:3.12-slim`에는 시스템 tzdata가 없다. `requirements.txt`의 `tzdata`를
  빼면 `zoneinfo` 에러가 난다 (Asia/Seoul 처리에 필수)
- ICS의 all-day 일정은 반드시 DATE 타입(시간 없음)이어야 한다. 틀리면 아이폰에서
  시간 지정 일정으로 보인다
- `collectstatic`은 이미지 빌드 때 돌아간다. settings를 임포트할 수 있어야 하므로
  Dockerfile에서 더미 env를 넣어 실행한다
- 반복 일정(RRULE)은 아직 펼치지 않는다. 원본 1건만 들어오고 sync 로그에 개수가 남는다

## 사용자 설정 화면 (`/settings/`)

일반 사용자가 admin 없이 스스로 하는 곳. `calendars/views.py`의 `settings_page`.

- 자기 피드 주소 확인·재발급·폐기 (재발급은 시간당 10회 제한)
- 외부 ICS 소스 등록·삭제·동기화 토글 (사용자당 5개, `select_for_update`로 경쟁 방지)
- **`@never_cache` 필수** — 본문에 피드 토큰이 들어가는 유일한 페이지다
- 소스 URL은 목록에서 마스킹한다(구글 비공개 주소는 비밀번호급)
- 등록 시점 검증은 https·호스트명까지만이고 **사설 IP 차단은 fetch 시점**에 걸린다.
  즉 `https://10.0.0.1/a.ics`는 등록되지만 sync에서 거부되고 백오프된다

## 명령

```bash
# 컨테이너 안에서 실행한다 (호스트에는 파이썬 환경이 없다)
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py sync_calendars --force
```
