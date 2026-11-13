from ninja import Schema


class ErrorOut(Schema):
    """Body of every non-2xx response."""

    code: str
    message: str


class HealthOut(Schema):
    status: str
    api_version: str
