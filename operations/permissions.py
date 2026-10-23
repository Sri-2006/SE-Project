from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

def role(user):
    if not user.is_authenticated:
        return ''
    if user.is_superuser:
        return 'Administrator'
    names = set(user.groups.values_list('name', flat=True))
    return next((name for name in ('Administrator', 'Manager', 'Planner') if name in names), '')

def allowed(*roles):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if role(request.user) not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate

ALL_ROLES = ('Planner', 'Manager', 'Administrator')
MANAGEMENT = ('Manager', 'Administrator')

