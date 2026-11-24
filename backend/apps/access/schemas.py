from ninja import Schema


class ServerAddressesOut(Schema):
    urls: list[str]
    public_urls: list[str]
