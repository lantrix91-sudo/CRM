from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import User
from apps.customers.models import Client
from apps.leads.models import Lead
from apps.orders.models import Order

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ("name", "phone", "district")

class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ("title", "client", "service", "source")
        widgets = {"source": forms.Select(choices=[("", "Не указан")] + [(s, s) for s in ("OLX", "Google", "Instagram", "Telegram", "Телефон")])}

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ("title", "client", "service", "employee", "amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = User.objects.filter(role="worker", is_active=True)
        if self.instance.pk and self.instance.status not in ("new", "assigned"):
            self.fields["employee"].disabled = True
        if self.instance.status == "paid":
            self.fields["amount"].disabled = True

class EmployeeForm(forms.ModelForm):
    password = forms.CharField(label="Пароль (для нового сотрудника обязателен)", widget=forms.PasswordInput, required=False)
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "role", "is_active", "services", "is_available", "max_active_leads", "telegram_chat_id")

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not self.instance.pk and not password:
            raise forms.ValidationError("Укажите пароль.")
        if password:
            validate_password(password, self.instance)
        return password

    def clean_role(self):
        role = self.cleaned_data["role"]
        if not role:
            raise forms.ValidationError("Выберите роль.")
        return role

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class CallResolveForm(forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.none(), label="Клиент")

    def __init__(self, *args, phone, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(normalized_phone=phone)


from apps.services.models import Service

class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ("name", "description")


class ReceivedPaymentForm(forms.Form):
    received_amount = forms.DecimalField(label="Полученная сумма (KZT)", max_digits=12, decimal_places=2, min_value=0.01)
    received_method = forms.ChoiceField(label="Способ оплаты", choices=(("", "Выберите способ"), ("cash", "Наличные"), ("transfer", "Перевод"), ("card", "Карта")))
