from django.conf import settings
from django.db import migrations


def rename_onefit_organization(apps, schema_editor):
    Organization = apps.get_model('core', 'Organization')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    try:
        owner = User.objects.get(username__iexact='onefit')
        Organization.objects.filter(owner=owner).update(name='Pinnacle Performance')
    except User.DoesNotExist:
        pass

    Organization.objects.filter(name__iexact='onefit').update(name='Pinnacle Performance')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_add_event_performance_indexes'),
    ]

    operations = [
        migrations.RunPython(rename_onefit_organization, migrations.RunPython.noop),
    ]