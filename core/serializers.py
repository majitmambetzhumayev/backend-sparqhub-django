# core/serializers.py
from rest_framework import serializers
from assistants.serializers import AssistantSerializer

class QuickChatMetadataSerializer(serializers.Serializer):
    assistants         = AssistantSerializer(many=True)
    default_assistant  = serializers.IntegerField(allow_null=True)
    default_thread     = serializers.IntegerField(allow_null=True)
