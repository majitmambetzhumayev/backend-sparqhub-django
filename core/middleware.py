# core/middleware.py

# This app doesn't use any of these browser features — explicitly denying
# them is defense-in-depth against a future XSS pulling in unexpected
# capabilities, not a response to any concrete need today.
_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def permissions_policy_middleware(get_response):
    def middleware(request):
        response = get_response(request)
        response['Permissions-Policy'] = _PERMISSIONS_POLICY
        return response

    return middleware
