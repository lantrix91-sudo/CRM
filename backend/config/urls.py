from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.leads.api import BoardAPI, LeadStatusAPI, LeadAssignAPI, OrderAssignAPI
from apps.leads.views import kanban
from apps.accounts import views as accounts
from django.contrib.auth.views import LoginView, LogoutView
from apps.leads.telephony import IncomingCallAPI
from apps.leads import reports

urlpatterns = [
    path("api/mobile/", include("apps.accounts.mobile_urls")),
    path("settlements/", accounts.settlements, name="settlements"),
    path("settlements/<int:pk>/", accounts.settlements, name="worker-settlement"),
    path("settlements/shifts/<int:pk>/", accounts.settlement_shift_detail, name="settlement-shift-detail"),
    path("settlements/export/", accounts.settlement_export, name="settlement-export"),
    path("settlements/<int:pk>/export/", accounts.settlement_export, name="worker-settlement-export"),
    path("profile/", accounts.profile, name="profile"),
    path("my-orders/history/", accounts.worker_history, name="worker-history"),
    path("api/orders/<int:pk>/assign/", OrderAssignAPI.as_view()),
    path("", accounts.home),
    path("leads/", include("apps.leads.urls")),
    path("kanban/", kanban, name="kanban"),
    path("api/leads/<int:pk>/assign/", LeadAssignAPI.as_view(), name="lead-assign-api"),
    path("api/leads/board/", BoardAPI.as_view(), name="lead-board-api"),
    path("api/leads/<int:pk>/status/", LeadStatusAPI.as_view(), name="lead-status-api"),
    path("api/telephony/incoming/", IncomingCallAPI.as_view()),
    path("calls/<int:pk>/resolve/", accounts.resolve_call, name="resolve-call"),
    path("calls/", reports.calls, name="calls"),
    path("clients/", accounts.clients, name="clients"),
    path("clients/<int:pk>/history/", reports.history, name="client-history"),
    path("analytics/", reports.analytics, name="analytics"),
    path("login/", LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("manager/", accounts.manager_home, name="manager-home"),
    path("operator/", accounts.operator_home, name="operator-home"),
    path("my-assignments/<int:pk>/", accounts.assigned_lead_detail, name="assigned-lead-detail"),
    path("my-orders/", accounts.worker_home, name="worker-home"),
    path("orders/<int:pk>/", accounts.order_detail, name="order-detail"),
    path("workspace/<str:kind>/new/", accounts.edit_record, name="record-new"),
    path("workspace/<str:kind>/<int:pk>/", accounts.edit_record, name="record-edit"),
    path("employees/", accounts.employees, name="employees"),
    path("employees/<int:pk>/", accounts.employees, name="employee-edit"),
    path("admin/", admin.site.urls),
]

# Technical administration is reserved for superusers.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
