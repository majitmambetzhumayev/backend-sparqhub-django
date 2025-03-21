from django.db import models
from threads.models import Thread

SENDER_CHOICES = (
    ('user', 'User'),
    ('assistant', 'Assistant'),
)

class Message(models.Model):
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    edited = models.BooleanField(default=False)
    read = models.BooleanField(default=False)

    def __str__(self):
        return f"Message {self.id} in Thread {self.thread.id}"
