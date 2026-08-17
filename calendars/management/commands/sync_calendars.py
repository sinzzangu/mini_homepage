"""등록된 외부 ICS 소스를 가져온다. systemd timer가 15분마다 호출한다."""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from calendars.ics_fetch import SourceError
from calendars.models import CalendarSource
from calendars.sync import record_failure, sync_source


class Command(BaseCommand):
    help = "등록된 외부 ICS 소스를 내려받아 일정을 갱신한다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-id", type=int, help="이 소스 하나만 동기화한다."
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="백오프로 미뤄둔 소스도 지금 시도한다.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        sources = CalendarSource.objects.filter(is_active=True).select_related("owner")
        if options["source_id"]:
            sources = sources.filter(pk=options["source_id"])
        if not options["force"]:
            sources = sources.filter(
                Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
            )

        if not sources:
            self.stdout.write("동기화할 소스가 없습니다.")
            return

        failures = 0
        for source in sources:
            label = f"[{source.id}] {source.name} ({source.owner.username})"
            try:
                stats = sync_source(source, now=now)
            except SourceError as exc:
                failures += 1
                delay = record_failure(source, str(exc), now=now)
                self.stderr.write(
                    self.style.ERROR(f"{label} 실패: {exc} → {delay}분 후 재시도")
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{label} {stats['status']}"))

        if failures:
            self.stdout.write(f"실패 {failures}건.")
