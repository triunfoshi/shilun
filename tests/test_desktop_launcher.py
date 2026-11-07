"""Run the real desktop launcher with isolated macOS command substitutes."""

import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "石论后台.app/Contents/MacOS/shilun-launcher"
BACKEND = "cn.shilun.backend"
OPTIONAL = (
    "cn.shilun.minute-bars",
    "cn.shilun.tushare-daily",
    "cn.shilun.holding-check",
    "cn.shilun.trading-intraday",
    "cn.shilun.structure-samples",
)
URL = "http://127.0.0.1:8000/ui"
ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(ZSH is None, reason="Desktop launcher requires zsh")

# Only OS command behavior is simulated. The production shell script retains its
# control flow, filesystem checks, error handling, and exit status.
FAKE_TOOL = r'''
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["LAUNCHER_TEST_STATE"])
state = json.loads(state_path.read_text())
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
code = 0
if tool == "curl":
    code = 0 if state["ready"] else 22
elif tool == "lsof":
    code = 0 if state["port_in_use"] else 1
elif tool == "launchctl":
    command = args[0]
    if command == "print-disabled":
        print("disabled services = {")
        for label, value in state["disabled"].items():
            print(f'  "{label}" => {value}')
        print("}")
    elif command == "print":
        code = 0 if args[-1].rsplit("/", 1)[-1] in state["loaded"] else 113
    elif command == "bootstrap":
        label = Path(args[-1]).stem
        if label in state["fail_bootstrap"]:
            print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
            code = 5
        else:
            state["loaded"].append(label)
            if label == "cn.shilun.backend":
                state["ready"] = True
    elif command == "kickstart":
        state["ready"] = True
    else:
        code = 99
elif tool == "open":
    code = state["open_exit"]
elif tool == "id":
    print("501")
elif tool == "date":
    print("2026-11-02 12:00:00")
elif tool == "mkdir":
    Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif tool not in ("osascript", "sleep"):
    code = 99

state_path.write_text(json.dumps(state))
with Path(os.environ["LAUNCHER_TEST_EVENTS"]).open("a") as output:
    output.write(json.dumps({"tool": tool, "args": args, "returncode": code}) + "\n")
sys.exit(code)
'''


@pytest.fixture
def launch(tmp_path):
    def run(*, missing=(), **scenario):
        project = tmp_path / "isolated project"
        resources = project / "石论后台.app/Contents/Resources"
        resources.mkdir(parents=True)
        python = project / ".venv312/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        for label in (BACKEND, *OPTIONAL):
            if label not in missing:
                (resources / f"{label}.plist").write_bytes(plistlib.dumps({
                    "Label": label,
                    "ProgramArguments": [str(python), "-c", "raise SystemExit(99)"],
                }))

        state = {
            "ready": True,
            "port_in_use": False,
            "loaded": [],
            "disabled": {},
            "fail_bootstrap": [],
            "open_exit": 0,
            **scenario,
        }
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state))
        events_path = tmp_path / "events.jsonl"
        tools = tmp_path / "fake commands"
        tools.mkdir()
        executable = tools / "fake-tool"
        executable.write_text(f"#!{sys.executable}\n{FAKE_TOOL}")
        executable.chmod(0o755)

        source, count = re.subn(
            r'^PROJECT_ROOT="[^"\n]+"$',
            lambda _: f"PROJECT_ROOT={shlex.quote(str(project))}",
            LAUNCHER.read_text(),
            count=1,
            flags=re.MULTILINE,
        )
        assert count == 1, "Launcher project root must be redirected to the fixture"
        paths = re.findall(r"/(?:usr/)?s?bin/[\w.-]+", source)
        allowed = {"curl", "launchctl", "open", "osascript", "lsof", "sleep", "id", "date", "mkdir", "zsh", "python"}
        for path in sorted(set(paths), key=len, reverse=True):
            name = Path(path).name
            assert name in allowed, f"Provide a safe substitute for new launcher command: {path}"
            if name in {"zsh", "python"}:
                continue  # Interpreter paths are not shell commands in the script.
            (tools / name).symlink_to(executable)
            source = source.replace(path, shlex.quote(str(tools / name)))

        script = tmp_path / "launcher.zsh"
        script.write_text(source)
        completed = subprocess.run(
            [ZSH, "-f", str(script)],
            env={
                **os.environ,
                "SHILUN_SKIP_OPEN": "0",
                "LAUNCHER_TEST_STATE": str(state_path),
                "LAUNCHER_TEST_EVENTS": str(events_path),
                "ZDOTDIR": str(tmp_path),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        log_path = project / "runtime/shilun-backend.log"
        return SimpleNamespace(
            returncode=completed.returncode,
            output=completed.stdout + completed.stderr,
            events=events,
            log=log_path.read_text() if log_path.exists() else "",
            resources=resources,
        )

    return run


def calls(result, tool, command=None):
    return [event for event in result.events if event["tool"] == tool
            and (command is None or event["args"][0] == command)]


def bootstrapped_labels(result):
    return [Path(event["args"][-1]).stem for event in calls(result, "launchctl", "bootstrap")]


def test_ready_backend_opens_before_optional_agents_and_preserves_disabled_jobs(launch):
    disabled = {OPTIONAL[0]: "disabled", OPTIONAL[1]: "true"}
    result = launch(disabled=disabled)

    assert result.returncode == 0, result.output
    assert calls(result, "open")[0]["args"] == [URL]
    assert result.events.index(calls(result, "open")[0]) < result.events.index(calls(result, "launchctl")[0])
    assert bootstrapped_labels(result) == list(OPTIONAL[2:])
    assert not calls(result, "lsof")
    assert not calls(result, "launchctl", "enable")
    for label in disabled:
        assert not any(label in " ".join(event["args"]) for event in calls(result, "launchctl"))
        assert f"跳过已禁用的附属任务：{label}" in result.log


def test_cold_backend_becomes_ready_and_opens_before_optional_registration(launch):
    result = launch(ready=False)

    assert result.returncode == 0, result.output
    registrations = calls(result, "launchctl", "bootstrap")
    assert bootstrapped_labels(result) == [BACKEND, *OPTIONAL]
    assert registrations[0]["args"] == ["bootstrap", "gui/501", str(result.resources / f"{BACKEND}.plist")]
    probes = calls(result, "curl")
    assert [event["returncode"] for event in probes] == [22, 0]
    backend_started = result.events.index(registrations[0])
    ready = result.events.index(probes[-1])
    opened = result.events.index(calls(result, "open")[0])
    optional_started = result.events.index(registrations[1])
    assert backend_started < ready < opened < optional_started


@pytest.mark.parametrize("problem", ["registration_failed", "missing_plist"])
def test_optional_agent_problem_is_logged_and_does_not_block_the_console(launch, problem):
    label = OPTIONAL[0]
    options = {"fail_bootstrap": [label]} if problem == "registration_failed" else {"missing": [label]}
    result = launch(**options)

    assert result.returncode == 0, result.output
    assert len(calls(result, "open")) == 1
    assert not calls(result, "osascript")
    assert OPTIONAL[-1] in bootstrapped_labels(result), "A failed optional agent must not stop later agents"
    expected = "附属任务注册失败" if problem == "registration_failed" else "附属任务配置缺失"
    assert f"{expected}：{label}" in result.log
    assert "网页入口不受影响" in result.log
    if problem == "missing_plist":
        assert label not in bootstrapped_labels(result)


def test_registered_optional_agents_are_not_bootstrapped_again(launch):
    result = launch(loaded=list(OPTIONAL))

    assert result.returncode == 0, result.output
    assert len(calls(result, "open")) == 1
    assert bootstrapped_labels(result) == []
    assert len(calls(result, "launchctl", "print")) == len(OPTIONAL)


def test_registered_backend_is_kickstarted_when_not_ready(launch):
    result = launch(ready=False, loaded=[BACKEND, *OPTIONAL])

    assert result.returncode == 0, result.output
    restarted = calls(result, "launchctl", "kickstart")
    assert [event["args"] for event in restarted] == [["kickstart", "-k", f"gui/501/{BACKEND}"]]
    assert bootstrapped_labels(result) == []
    assert result.events.index(restarted[0]) < result.events.index(calls(result, "open")[0])


@pytest.mark.parametrize("failure", ["bootstrap", "occupied_port"])
def test_backend_failure_reports_error_and_never_opens_or_starts_optional_agents(launch, failure):
    options = {"fail_bootstrap": [BACKEND]} if failure == "bootstrap" else {"port_in_use": True}
    result = launch(ready=False, **options)

    assert result.returncode != 0
    assert calls(result, "open") == []
    assert not set(bootstrapped_labels(result)) & set(OPTIONAL)
    assert not calls(result, "launchctl", "print-disabled")
    errors = calls(result, "osascript")
    assert len(errors) == 1
    expected = "后台服务注册失败" if failure == "bootstrap" else "端口 8000 已被其他程序占用"
    assert expected in errors[0]["args"][-1]
    if failure == "occupied_port":
        assert not calls(result, "launchctl")


@pytest.mark.parametrize("ready", [True, False])
def test_browser_open_failure_is_not_reported_as_success(launch, ready):
    result = launch(ready=ready, open_exit=1)

    assert result.returncode != 0
    assert len(calls(result, "open")) == 1
    assert "默认浏览器未能打开" in result.log
    assert "默认浏览器未能打开页面" in calls(result, "osascript")[0]["args"][-1]
    assert not calls(result, "launchctl", "print-disabled")
    assert not set(bootstrapped_labels(result)) & set(OPTIONAL)
