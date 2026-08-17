FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# admin CSS 등 정적 파일을 미리 모아둔다(WhiteNoise가 서빙).
# settings 임포트만 하면 되므로 DB 접속은 필요 없다 — 더미 값으로 충분.
RUN DJANGO_SECRET_KEY=build-only \
    POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
    python manage.py collectstatic --noinput

# 1 GB box: one worker with threads instead of multiple processes.
# 시작할 때 마이그레이션을 먼저 적용한다.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn minihomepage.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4"]
