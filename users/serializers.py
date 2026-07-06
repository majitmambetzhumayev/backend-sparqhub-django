# users/serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()

class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'password']

    def create(self, validated_data):
        # create_user (not a plain .save()) is required to hash the password.
        user = User.objects.create_user(**validated_data)
        return user

class CurrentUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'bio',
            'profile_picture',
            'timezone',
            'preferred_integration',
            'phone_number',
            'credits_remaining',
            'is_staff',
        ]


class AdminUserSerializer(serializers.ModelSerializer):
    # Only credits_remaining and is_active are writable here — role/permission
    # changes (is_staff, is_superuser) aren't exposed by this panel.
    credits_remaining = serializers.IntegerField(min_value=0)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'credits_remaining',
            'is_active',
            'is_staff',
            'date_joined',
            'last_login',
        ]
        read_only_fields = ['id', 'username', 'email', 'is_staff', 'date_joined', 'last_login']