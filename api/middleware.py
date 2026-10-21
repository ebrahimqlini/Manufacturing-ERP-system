class APINoStoreMiddleware:
    """Keep API responses fresh for mobile dashboards and order screens."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/v1/"):
            response["Cache-Control"] = "no-store"
            response["Pragma"] = "no-cache"
        return response
