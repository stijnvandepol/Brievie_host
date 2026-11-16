from ninja import Schema


class AiStatusOut(Schema):
    # `off`, `external` or `local`; nothing about the service itself.
    mode: str
