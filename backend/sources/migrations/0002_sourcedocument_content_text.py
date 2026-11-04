from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sources', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='sourcedocument',
            name='content_text',
            field=models.TextField('정제된 공식자료 본문', blank=True, default=''),
        ),
    ]
