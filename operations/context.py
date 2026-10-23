from django.utils import timezone
from .models import Alert, AuditLog
from .permissions import role

def navigation(request):
    current_role = role(request.user)
    return {'current_role': current_role, 'can_manage': current_role in ('Manager', 'Administrator'),
            'today': timezone.localdate(),
            'demo_data': AuditLog.objects.filter(action='Demo workspace created').exists() if current_role else False,
            'unread_alerts': Alert.objects.filter(active=True).exclude(read_by=request.user).count() if current_role else 0}

