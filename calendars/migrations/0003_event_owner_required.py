"""owner를 필수로 만든다.

0002에서 nullable로 추가한 뒤 기존 일정을 juanb에게 귀속시켰으므로
이 시점에는 NULL인 행이 없다.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("calendars", "0002_event_owner_feedtoken"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
