from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from operations.views import SignInView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', SignInView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('account/password/', auth_views.PasswordChangeView.as_view(template_name='form.html', success_url='/'), name='password_change'),
    path('', include('operations.urls')),
]
handler403 = 'operations.views.forbidden'
handler404 = 'operations.views.not_found'
handler500 = 'operations.views.server_error'

