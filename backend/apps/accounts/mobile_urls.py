from django.urls import path
from . import mobile_api as api
from . import mobile_staff as staff

urlpatterns = [
    path("me/", api.Identity.as_view()),
    path("staff/options/", staff.Options.as_view()),
    path("staff/leads/", staff.Leads.as_view()),
    path("staff/leads/<int:pk>/", staff.LeadDetail.as_view()),
    path("staff/orders/", staff.Orders.as_view()),
    path("staff/orders/<int:pk>/", staff.OrderDetail.as_view()),
    path("staff/clients/", staff.Clients.as_view()),
    path("staff/dashboard/", staff.Dashboard.as_view()),
    path("staff/team/", staff.Team.as_view()),
    path("staff/team/<int:pk>/availability/", staff.WorkerAvailability.as_view()),
    path("login/", api.Login.as_view()),
    path("logout/", api.Logout.as_view()),
    path("profile/", api.Profile.as_view()),
    path("orders/", api.Orders.as_view()),
    path("orders/<int:pk>/", api.OrderDetail.as_view()),
    path("orders/<int:pk>/action/", api.OrderAction.as_view()),
]
