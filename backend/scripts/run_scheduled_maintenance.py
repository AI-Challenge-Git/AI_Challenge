import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

KOREA_TIME_ZONE = ZoneInfo("Asia/Seoul")
KRX_REFRESH_HOUR_KST = 14
PURGE_COMMAND = (
    sys.executable,
    "-m",
    "scripts.purge_data",
    "--execute",
    "--batch-size",
    "100",
)
KRX_COMMAND = (sys.executable, "-m", "scripts.refresh_krx_symbols")


def _run_command(command: tuple[str, ...]) -> int:
    return subprocess.run(command, check=False).returncode  # noqa: S603


def run(*, now: datetime | None = None) -> int:
    current = now or datetime.now(KOREA_TIME_ZONE)
    return_codes = [_run_command(PURGE_COMMAND)]
    if current.astimezone(KOREA_TIME_ZONE).hour == KRX_REFRESH_HOUR_KST:
        return_codes.append(_run_command(KRX_COMMAND))
    return 1 if any(return_codes) else 0


if __name__ == "__main__":
    raise SystemExit(run())
