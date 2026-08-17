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

## 실행

```bash
cp .env.example .env          # 값 채우기
docker compose -f deploy/docker-compose.example.yml up -d --build
```

마이그레이션은 컨테이너가 뜰 때 자동 적용된다. 관리자 계정은
`docker compose exec web python manage.py createsuperuser`로 만든다.

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

## 스택

Django 5.2 · PostgreSQL 16 · gunicorn · Caddy 2 · WhiteNoise ·
[icalendar](https://pypi.org/project/icalendar/) · [FullCalendar](https://fullcalendar.io/) 6
