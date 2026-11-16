from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('documents', '0006_interpretationresult_call_script')]

    operations = [
        migrations.AddField(
            model_name='document', name='analysis_attempt_id',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
    ]
