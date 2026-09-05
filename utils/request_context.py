from contextvars import ContextVar

request_id = ContextVar("request_id", default="-")


def current_request_id():
    return request_id.get()
