# Marks all existing users as email-verified so the existing user base is
# never locked out once REQUIRE_EMAIL_VERIFICATION is enabled.
# New users still start with email_verified=False until they confirm via the
# verification link sent at registration.

from django.db import migrations


def mark_existing_users_verified(apps, schema_editor):
    User = apps.get_model('apiApp', 'User')
    User.objects.filter(email_verified=False).update(email_verified=True)


def unmark_existing_users(apps, schema_editor):
    # Reverse: leave the flag as the schema default (False). Kept for
    # migration reversibility; not expected to be used in practice.
    User = apps.get_model('apiApp', 'User')
    User.objects.filter(email_verified=True).update(email_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ('apiApp', '0036_user_email_verified'),
    ]

    operations = [
        migrations.RunPython(mark_existing_users_verified, unmark_existing_users),
    ]
