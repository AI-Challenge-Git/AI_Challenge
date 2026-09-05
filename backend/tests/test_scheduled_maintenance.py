from datetime import datetime
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

from scripts import run_scheduled_maintenance

KST = ZoneInfo("Asia/Seoul")


def test_hourly_maintenance_runs_purge_without_krx_outside_refresh_hour(
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def succeed(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(run_scheduled_maintenance, "_run_command", succeed)

    result = run_scheduled_maintenance.run(now=datetime(2026, 9, 5, 13, 17, tzinfo=KST))

    assert result == 0
    assert commands == [run_scheduled_maintenance.PURGE_COMMAND]


def test_hourly_maintenance_runs_both_jobs_at_krx_refresh_hour(
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def succeed(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(run_scheduled_maintenance, "_run_command", succeed)

    result = run_scheduled_maintenance.run(now=datetime(2026, 9, 5, 14, 17, tzinfo=KST))

    assert result == 0
    assert commands == [
        run_scheduled_maintenance.PURGE_COMMAND,
        run_scheduled_maintenance.KRX_COMMAND,
    ]


def test_hourly_maintenance_runs_krx_even_when_purge_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fail_purge(command: tuple[str, ...]) -> int:
        commands.append(command)
        return 1 if command == run_scheduled_maintenance.PURGE_COMMAND else 0

    monkeypatch.setattr(run_scheduled_maintenance, "_run_command", fail_purge)

    result = run_scheduled_maintenance.run(now=datetime(2026, 9, 5, 14, 17, tzinfo=KST))

    assert result == 1
    assert commands == [
        run_scheduled_maintenance.PURGE_COMMAND,
        run_scheduled_maintenance.KRX_COMMAND,
    ]
