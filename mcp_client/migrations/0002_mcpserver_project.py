from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_initial'),
        ('mcp_client', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mcpserver',
            name='assistant',
        ),
        migrations.AddField(
            model_name='mcpserver',
            name='project',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='mcp_servers',
                to='projects.project',
            ),
            preserve_default=False,
        ),
    ]
