from django.urls import path
from . import mobile_api as api

urlpatterns = [
    path("login/", api.Login.as_view()),
    path("logout/", api.Logout.as_view()),
    path("profile/", api.Profile.as_view()),
    path("orders/", api.Orders.as_view()),
    path("orders/<int:pk>/", api.OrderDetail.as_view()),
    path("orders/<int:pk>/action/", api.OrderAction.as_view()),
]
