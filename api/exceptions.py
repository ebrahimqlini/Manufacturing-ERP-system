from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Return a stable API error envelope while preserving DRF status codes."""
    response = exception_handler(exc, context)
    if response is None:
        return None
    response.data = {
        "error": {
            "status": response.status_code,
            "detail": response.data,
        }
    }
    return response
