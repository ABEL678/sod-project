from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Единый конверт ошибок API: {"errors": {...}}."""
    response = exception_handler(exc, context)
    if response is not None:
        response.data = {"errors": response.data}
    return response
