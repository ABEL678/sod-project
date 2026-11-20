from django.urls import path

from . import views

app_name = 'help'

urlpatterns = [
    path('', views.HelpHomeView.as_view(), name='home'),
    path('<slug:slug>/', views.HelpArticleView.as_view(), name='article'),
]
