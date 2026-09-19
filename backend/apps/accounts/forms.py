from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import User
from apps.customers.models import Client
from apps.customers.models import City
from apps.leads.models import Lead
from apps.orders.models import Order

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ("name", "phone", "address", "district")

    def clean_phone(self):
        from apps.customers.phones import normalize_phone
        try:
            return normalize_phone(self.cleaned_data["phone"])
        except ValueError as error:
            raise forms.ValidationError(str(error))

class LeadForm(forms.ModelForm):
    scheduled_at = forms.DateTimeField(label="Дата и время записи", required=False,
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        help_text="Необязательно. Время CRM (Asia/Qyzylorda). Мастеру придёт напоминание в Telegram.")
    client_name = forms.CharField(label="Имя", max_length=200)
    client_phone = forms.CharField(label="Номер телефона", max_length=32)
    client_address = forms.CharField(label="Адрес, квартира", max_length=300, required=False)
    city = forms.ModelChoiceField(queryset=City.objects.filter(is_active=True), label="Город")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.client_id:
            client = self.instance.client
            self.initial.update(client_name=client.name, client_phone=client.phone, client_address=client.address)

    def clean_client_phone(self):
        from apps.customers.phones import normalize_phone
        try:
            return normalize_phone(self.cleaned_data["client_phone"])
        except ValueError as error:
            raise forms.ValidationError(str(error))

    def save(self, commit=True):
        from django.db import transaction
        if not commit:
            raise ValueError("Saving a client and lead requires commit=True")
        with transaction.atomic():
            phone = self.cleaned_data["client_phone"]
            client = Client.objects.filter(normalized_phone=phone).order_by("pk").first()
            if client is None:
                client = Client.objects.create(
                    name=self.cleaned_data["client_name"], phone=phone,
                    address=self.cleaned_data.get("client_address", ""),
                )
            self.instance.client = client
            if not self.instance.title:
                self.instance.title = self.cleaned_data["client_name"]
            return super().save(commit=commit)

    class Meta:
        model = Lead
        fields = ("city", "client_name", "client_address", "client_phone", "service", "appliance_type", "brand", "comment", "source", "scheduled_at")
        widgets = {"comment": forms.Textarea(attrs={"rows": 3}), "source": forms.Select(choices=[("", "Не указан")] + [(s, s) for s in ("OLX", "Google", "Instagram", "Telegram", "Телефон")])}

class OrderForm(forms.ModelForm):
    city = forms.ModelChoiceField(queryset=City.objects.filter(is_active=True), label="Город")

    class Meta:
        model = Order
        fields = ("city", "title", "client", "service", "employee", "amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = User.objects.filter(role="worker", is_active=True)
        if self.instance.pk and (self.instance.status != "new" or self.instance.employee_id):
            self.fields["employee"].disabled = True
            self.fields["service"].disabled = True
            self.fields["employee"].help_text = "Для смены мастера верните заказ оператору на канбане."
        if self.instance.status == "paid" or self.instance.is_free or self.instance.repeat_of_id:
            self.fields["amount"].disabled = True

class EmployeeForm(forms.ModelForm):
    password = forms.CharField(label="Пароль (для нового сотрудника обязателен)", widget=forms.PasswordInput, required=False)
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "role", "curator", "is_active", "services", "service_cities", "is_available", "max_active_leads", "telegram_chat_id")

    def clean(self):
        data = super().clean()
        for field in ("curator",):
            if data.get(field) and data.get("role") != "worker":
                self.add_error(field, "Закрепление доступно только для мастера.")
        return data

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
    client = forms.ModelChoiceField(
        queryset=Client.objects.none(),
        label="Клиент",
    )

    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        label="Город",
    )

    def __init__(self, *args, phone, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(
            normalized_phone=phone
        )
from apps.services.models import Service

class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ("name", "description")


class ReceivedPaymentForm(forms.Form):
    received_amount = forms.DecimalField(label="Полученная сумма (KZT)", max_digits=12, decimal_places=2, min_value=0.01)
    received_method = forms.ChoiceField(label="Способ оплаты", choices=(("", "Выберите способ"), ("cash", "Наличные"), ("transfer", "Перевод"), ("card", "Карта")))


class OrderCompletionForm(forms.Form):
    amount = forms.DecimalField(label="Полученная сумма (KZT)", max_digits=12, decimal_places=2, min_value=0)
    expenses = forms.DecimalField(label="Расходы (KZT)", max_digits=12, decimal_places=2, min_value=0, initial=0)
    comment = forms.CharField(label="Что поменяли или сделали", max_length=2000)

    def clean(self):
        data = super().clean()
        if data.get("amount") is not None and data.get("expenses") is not None and data["expenses"] > data["amount"]:
            self.add_error("expenses", "Расходы не могут превышать полученную сумму.")
        return data
