import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

import django
django.setup()

from django.db import connection
tables = [t for t in connection.introspection.django_table_names() if 'designtask' in t]
for t in tables:
    print(t)
if not tables:
    print("NO DESIGN TASK TABLES FOUND")
