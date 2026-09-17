"""Create the first administrator only when explicitly provisioned."""
import os
import sys
sys.path.insert(0, "/app/backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import transaction

password = os.environ.get("CRM_BOOTSTRAP_PASSWORD")
if password:
    User = get_user_model()
    with transaction.atomic():
        if not User.objects.exists():
            User.objects.create_superuser(username="admin", password=password, role="manager")
            print("Initial administrator created")
        else:
            print("Bootstrap skipped: users already exist")
