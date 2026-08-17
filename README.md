# minihomepage

개인 서버에서 돌리는 작은 홈페이지. 지금은 **캘린더**가 첫 기능이다.

Oracle Always Free(1 OCPU / 1 GB RAM) 같은 작은 서버에서 돌아가는 것을 목표로
만들었다 — 무거운 의존성 없이 Django + PostgreSQL + Caddy 조합이다.

## 기능

### 캘린더 (`calendars` 앱)
- 월/주/일 뷰에서 드래그로 일정 생성·수정·삭제 (FullCalendar 6, CDN)
- 사용자별로 자기 캘린더만 본다. 남의 일정은 조회조차 안 된다
- **내보내기**: `/feed.ics?token=…` — 구글 캘린더·아이폰 캘린더가 구독할 수 있는
  표준 iCalendar 피드. 토큰이 사용자를 결정하고, 사용자당 여러 개 발급·개별 폐기 가능
- **가져오기**: 외부 ICS(구글 비공개 주소, 아이클라우드 공유 링크)를 등록하면
  15분마다 읽어와 소스별 색으로 표시한다. 가져온 일정은 읽기 전용 미러다
- 시간대는 Asia/Seoul 고정, 종일 일정은 ICS DATE 타입으로 주고받는다

## 설치

```bash
cp .env.example .env          # 값 채우기
docker compose -f deploy/docker-compose.example.yml up -d --build
```

마이그레이션은 컨테이너가 뜰 때 자동 적용된다. 관리자 계정을 하나 만든다:

```bash
docker compose exec web python manage.py createsuperuser
```

외부 ICS를 15분마다 가져오려면 systemd 타이머를 등록한다
(`deploy/` 예시 참고, 또는 cron에 `manage.py sync_calendars`를 걸어도 된다).

---

# 사용 방법

아래에서 `cal.example.com`은 캘린더 서브도메인, `example.com`은 메인 도메인이다.
본인 도메인으로 바꿔 읽으면 된다. 메인 도메인의 `/cal/` 경로로도 똑같이 동작한다.

## 1. 계정 만들기

`https://cal.example.com/signup/` 에서 아이디와 비밀번호로 가입한다.
가입하면 **자기 캘린더**와 **구독용 피드 토큰 1개**가 자동으로 생긴다.

비밀번호는 8자 이상이어야 하고, 아이디와 비슷하거나 흔한 비밀번호·숫자만으로는 거부된다.
가입 시도는 IP당 시간당 5회로 제한된다.

로그인은 `https://cal.example.com/login/`. 로그인하지 않고 캘린더에 들어가면
자동으로 로그인 화면으로 보내진다.

## 2. 일정 만들고 고치기

캘린더 화면(`https://cal.example.com/`)에서 마우스로 한다.

| 하고 싶은 것 | 방법 |
|---|---|
| 일정 만들기 | 빈 칸을 **클릭**하거나 여러 칸을 **드래그** → 제목 입력 |
| 제목 바꾸기 | 일정을 **클릭** → 새 제목 입력 |
| 일정 삭제 | 일정을 **클릭** → 제목을 **비우고** 확인 → 삭제할지 한 번 더 물어본다 |
| 날짜·시간 옮기기 | 일정을 **드래그** |
| 길이 늘이기 | 일정의 아래(또는 옆) 끝을 **드래그** |
| 뷰 바꾸기 | 오른쪽 위 `월` / `주` / `일` 버튼 |
| 오늘로 이동 | 왼쪽 위 `오늘` 버튼 |

시간대는 Asia/Seoul로 고정되어 있다. 하루 종일 일정은 월 뷰에서 날짜를 드래그하면 만들어진다.

**색이 다르고 클릭해도 수정되지 않는 일정**은 외부에서 가져온 것이다(아래 4번).
원본 캘린더 앱에서 고쳐야 한다.

## 3. 내 일정을 폰·구글 캘린더에서 보기 (내보내기)

웹에서 만든 일정을 폰 캘린더 앱에 띄우는 방법이다. 폰이 주기적으로 피드 주소를
읽어가는 **구독** 방식이라, 폰에서는 **읽기 전용**으로 보인다.

### 먼저 피드 주소를 확인한다

```
https://cal.example.com/feed.ics?token=<64자리 토큰>
```

토큰은 가입할 때 자동 발급된다. 현재 값을 확인하는 화면은 아직 없어서
**관리자가 admin에서 알려줘야 한다** (`https://example.com/admin/` → Feed tokens →
해당 항목 → `token` 값). 일반 사용자용 화면은 만들 예정이다.

> ⚠️ **이 주소가 곧 비밀번호다.** 주소를 아는 사람은 그 사용자의 일정을 모두 볼 수 있다.
> 채팅·메일로 아무렇게나 공유하지 말 것. 새어 나갔으면 admin에서 해당 토큰을
> `is_active` 해제하고 새로 발급하면 이전 주소는 즉시 무효가 된다.

### 아이폰에 등록

설정 → 앱 → 캘린더 → 캘린더 계정 추가 → **기타** → **구독 캘린더 추가**
→ 피드 주소 붙여넣기.

새로고침 빈도를 직접 고를 수 있다(15분 등). 폰에서 새로고침하기 전에는 웹에서 만든
일정이 바로 보이지 않는 게 정상이다.

### 구글 캘린더에 등록 (PC 웹에서)

왼쪽 `다른 캘린더` 옆 `+` → **URL로 추가** → 피드 주소 붙여넣기.

구글은 외부 피드를 **몇 시간 간격으로만** 다시 읽는다. 즉시 반영되지 않는 것은
정상이며 서버 쪽에서 조절할 수 없다. 빨리 반영되길 원하면 아이폰 구독을 쓰는 편이 낫다.

## 4. 구글·아이폰 일정을 웹 캘린더에서 보기 (가져오기)

반대 방향이다. 외부 캘린더의 ICS 주소를 등록하면 서버가 **15분마다** 읽어와
웹 캘린더에 소스별 색으로 표시한다.

### 원본 주소 찾기

- **구글 캘린더**: 설정 → 내 캘린더 설정에서 해당 캘린더 선택 → `캘린더 통합`
  → **비공개 주소(iCal 형식)** 복사
- **아이클라우드**: 캘린더 앱에서 해당 캘린더 공유 → **공개 캘린더** 활성화 →
  링크 복사 (`webcal://…` 형태. 그대로 붙여넣으면 `https://`로 자동 변환된다)

> ⚠️ 이 주소들도 비밀번호급이다. 특히 구글 "비공개 주소"는 그것만으로 캘린더 전체가
> 열리므로 코드나 메모에 남기지 말 것.

### 등록

현재는 관리자가 admin에서 등록한다 (`https://example.com/admin/` →
**Calendar sources** → `ADD`). 사용자용 관리 화면은 만들 예정이다.

| 항목 | 설명 |
|---|---|
| Owner | 이 일정을 볼 사용자 |
| Name | 구분용 이름 (예: `구글 개인`, `아내 아이폰`) |
| Url | 위에서 복사한 ICS 주소 |
| Color | 캘린더에 표시될 색 (`#rrggbb`) |
| Is active | 끄면 동기화를 멈춘다 |

저장하면 다음 동기화(최대 15분) 때 반영된다. 바로 확인하려면 서버에서:

```bash
docker compose exec web python manage.py sync_calendars --force
```

동기화 결과는 admin의 소스 목록 `Last status`에 남는다
(`성공: 신규 3, 갱신 0, 삭제 1` 형태). 실패하면 `Failure count`가 올라가고
다음 시도가 점점 뒤로 밀린다(15분 → 30분 → … 최대 6시간).

### 가져오기 규칙

- 가져온 일정은 **읽기 전용**이다. 웹에서 고칠 수 없고, 원본에서 지우면 여기서도 사라진다
- 내 피드(`/feed.ics`)로 **다시 내보내지 않는다** — 서로 구독하다 무한 루프가 생기는 것을 막는다
- 등록 가능한 주소는 **https만** (`webcal://`은 자동 변환). 사설망·내부 주소는 거부된다
- 사용자당 소스 **5개**까지
- 최근 90일 이전에 끝난 일정은 가져오지 않는다
- **반복 일정(매주 회의 등)은 첫 회차만 들어온다** — 아직 펼치지 않는다

## 5. 관리자가 쓰는 명령

```bash
# 계정 만들기
docker compose exec web python manage.py createsuperuser

# 외부 소스 즉시 동기화 (백오프 무시)
docker compose exec web python manage.py sync_calendars --force

# 특정 소스만
docker compose exec web python manage.py sync_calendars --source-id 3

# 로그
docker compose logs -f web
journalctl -u cal-sync.service -n 30    # systemd 타이머를 쓰는 경우

# DB 백업 (파일 복사로는 안 된다 — 볼륨 안에 있다)
docker compose exec -T db pg_dump -U <user> <db> > backup-$(date +%F).sql
```

## 아직 없는 것

- 일반 사용자가 **자기 피드 주소를 확인하는 화면** (지금은 admin에서만 확인 가능)
- 사용자가 **직접 외부 소스를 등록하는 화면** (지금은 admin에서만)
- 반복 일정(RRULE) 펼치기
- 사용자끼리 캘린더를 **공유해서 보는** 기능 (현재는 서로 완전히 격리)
- 웹에서 구글·아이폰 일정을 **직접 수정**하기 (구독은 읽기 전용. Google API/CalDAV가 필요)

---

## URL

`minihomepage/urls.py`(메인 도메인)와 `urls_cal.py`(캘린더 서브도메인)로 나뉘어 있고,
Host를 보고 고른다(`calendars/middleware.py`). 서브도메인에서는 캘린더가 루트에 온다.

| | 메인 도메인 | 캘린더 서브도메인 |
|---|---|---|
| 캘린더 | `/cal/` | `/` |
| 로그인·가입 | `/cal/login/`, `/cal/signup/` | `/login/`, `/signup/` |
| ICS 피드 | `/cal/feed.ics?token=…` | `/feed.ics?token=…` |
| API | `/cal/api/events` | `/api/events` |
| admin | `/admin/` | 없음 |

## 구조

```
minihomepage/          Django 설정 (settings·urls·wsgi)
calendars/             캘린더 기능
  ics_fetch.py         외부 ICS 다운로드 (SSRF 방어)
  sync.py              가져온 일정 upsert
  management/commands/sync_calendars.py
deploy/                compose·Caddy 배포 예시
```

기능을 더 붙일 때는 `calendars`처럼 Django 앱을 하나 추가하고
`minihomepage/urls.py`에 연결한다.

## 스택

Django 5.2 · PostgreSQL 16 · gunicorn · Caddy 2 · WhiteNoise ·
[icalendar](https://pypi.org/project/icalendar/) · [FullCalendar](https://fullcalendar.io/) 6
