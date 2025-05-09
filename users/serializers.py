# users/serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()

class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'password']  # Add other fields (like email) if needed.

    def create(self, validated_data):
        # Use create_user to handle password hashing and additional logic.
        user = User.objects.create_user(**validated_data)
        return user

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        # Add additional user data to the response
        data['user'] = {
            'id': self.user.id,
            'username': self.user.username,
            # Include any other minimal details you want to expose (e.g., email)
        }
        return data
    
class CurrentUserSerializer(serializers.ModelSerializer):
    profile_picture = serializers.SerializerMethodField()

    class Meta:
        model = User
        # Liste précise des champs existants sur CustomUser
        fields = [
            'id',
            'username',
            'bio',
            'profile_picture',
            'timezone',
            'preferred_integration',
            'phone_number',
        ]

    def get_profile_picture(self, obj):
        # Retourne l'URL ou None si non défini
        return obj.profile_picture