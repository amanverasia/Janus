import httpx
import respx

MOCK_DNS_IP = "93.184.216.34"


def mocked_route(method: str, url: str) -> respx.Route:
    parsed = httpx.URL(url)
    pinned = parsed.copy_with(host=MOCK_DNS_IP)
    return respx.route(method=method, url=pinned, headers={"Host": parsed.netloc.decode()})
