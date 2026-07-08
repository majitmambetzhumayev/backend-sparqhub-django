# users/oauth.py
from authlib.integrations.django_client import OAuth
from django.conf import settings

oauth = OAuth()

oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
    client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
    client_kwargs={'scope': 'openid email profile'},
)

# Not an OIDC provider — no server_metadata_url/userinfo endpoint, so the
# callback view has to fetch the profile (and possibly the emails list)
# itself via the api_base_url-relative client.get() calls below.
oauth.register(
    name='github',
    client_id=settings.GITHUB_OAUTH_CLIENT_ID,
    client_secret=settings.GITHUB_OAUTH_CLIENT_SECRET,
    access_token_url='https://github.com/login/oauth/access_token',
    authorize_url='https://github.com/login/oauth/authorize',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
)
