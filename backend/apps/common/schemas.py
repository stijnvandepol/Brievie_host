from ninja import Schema


class ErrorOut(Schema):
    """Body of every non-2xx response."""

    code: str
    message: str


class HealthOut(Schema):
    status: str
    api_version: str


# Documented error responses shared by many operations, merged into `response=`.
# Every operation that takes a JSON body can fail on the body itself.
BODY_ERRORS: dict[int, type[ErrorOut]] = {
    400: ErrorOut,  # bad_request: body is not valid JSON
    413: ErrorOut,  # payload_too_large
    415: ErrorOut,  # unsupported_media_type: body is not application/json
    422: ErrorOut,  # validation_error
}
# Operations that need a device token or a session.
AUTH_ERRORS: dict[int, type[ErrorOut]] = {401: ErrorOut}
# Session callers of state-changing operations also need a CSRF token.
SESSION_WRITE_ERRORS: dict[int, type[ErrorOut]] = {401: ErrorOut, 403: ErrorOut}
