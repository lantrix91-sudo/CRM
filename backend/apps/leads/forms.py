from django import forms
from .models import Lead


class LeadAdminForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        standard = ["OLX", "Google", "Instagram", "Telegram", "Телефон"]
        legacy = Lead.objects.exclude(source="").values_list("source", flat=True).distinct()
        values = list(dict.fromkeys(standard + list(legacy)))
        self.fields["source"].widget = forms.Select(choices=[("", "Не указан")] + [(s, s) for s in values])
