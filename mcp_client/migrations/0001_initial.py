from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('assistants', '0006_alter_assistant_ai_provider_alter_assistant_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='MCPServer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('transport', models.CharField(
                    choices=[('stdio', 'STDIO'), ('sse', 'SSE')],
                    default='stdio',
                    max_length=10,
                )),
                ('url', models.CharField(blank=True, max_length=500)),
                ('command', models.CharField(blank=True, max_length=255)),
                ('args', models.JSONField(default=list)),
                ('enabled', models.BooleanField(default=True)),
                ('assistant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='mcp_servers',
                    to='assistants.assistant',
                )),
            ],
        ),
    ]
