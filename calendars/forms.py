from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import CalendarSource


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username",)


class CalendarSourceForm(forms.ModelForm):
    """외부 ICS 소스 추가 폼.

    검증은 전부 모델에 맡긴다 — ModelForm이 `full_clean()`을 거치므로
    `clean_fields()`의 `normalize_url`(https 강제, webcal 변환)과
    `clean()`의 사용자당 5개 제한이 그대로 적용된다. 그래서 폼을 만들 때
    `instance=CalendarSource(owner=request.user)`로 소유자를 먼저 채워야 한다.
    """

    class Meta:
        model = CalendarSource
        fields = ("name", "url", "color")
        labels = {"name": "이름", "url": "ICS 주소", "color": "색"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "예: 구글 캘린더", "maxlength": 50}),
            # URLField가 아닌 이유는 모델과 같다 — type=url 위젯도 webcal://을
            # 브라우저 단계에서 거부하므로 일반 text로 받는다.
            "url": forms.TextInput(
                attrs={
                    "placeholder": "https://... 또는 webcal://...",
                    "autocomplete": "off",
                    "spellcheck": "false",
                }
            ),
            "color": forms.TextInput(attrs={"type": "color"}),
        }
