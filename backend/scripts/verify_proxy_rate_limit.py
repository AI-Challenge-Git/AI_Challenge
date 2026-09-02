import argparse
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.security import make_opaque_token


def _positive_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000:
        raise argparse.ArgumentTypeError("limit must be between 1 and 1000")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that Railway overwrites spoofed X-Real-IP before rate limiting"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--limit",
        required=True,
        type=_positive_limit,
        help="deployed SIGNAL_DASHBOARD_LIMIT value",
    )
    return parser.parse_args()


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be an exact public HTTPS origin")
    return value.rstrip("/")


def _request_status(url: str, token: str, spoofed_ip: str) -> int:
    request = Request(
        url,
        headers={
            "Accept": "application/json, application/problem+json",
            "Authorization": f"Bearer {token}",
            "X-Real-IP": spoofed_ip,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - HTTPS is validated above
            return int(response.status)
    except HTTPError as exc:
        return exc.code


def run(*, base_url: str, limit: int) -> int:
    origin = _validate_base_url(base_url)
    token = make_opaque_token()
    statuses = [
        _request_status(
            f"{origin}/api/signals/dashboard",
            token,
            f"198.51.100.{index % 250 + 1}",
        )
        for index in range(limit + 1)
    ]
    if statuses[:limit] != [200] * limit or statuses[-1] != 429:
        print(f"proxy_rate_limit_verified=false statuses={statuses}")
        return 1
    print("proxy_rate_limit_verified=true")
    return 0


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(run(base_url=arguments.base_url, limit=arguments.limit))
