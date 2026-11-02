"""Offline full-benchmark discovery, recovery, upload safety, and CLI regressions."""

import json
import time
from types import SimpleNamespace

import pytest
from test_benchmark_exchange import local_result
from test_core import HARDWARE

import RunFullbenchmark as runner


def options(tmp_path):
    return SimpleNamespace(
        context=4096,
        server="127.0.0.1",
        port=9443,
        ca_cert=None,
        allow_rootful_docker=False,
        family=["gemma3"],
        target=["cpu"],
        model_timeout=1,
        cleanup_timeout=1,
    )


def test_discovery_continues_and_filters_by_memory(tmp_path):
    class Catalog:
        def list(self):
            return ["broken", "gemma3"]

        def tags(self, family):
            if family == "broken":
                raise ValueError()
            return ["gemma3:small", "gemma3:small", "gemma3:large", "gemma3:cloud"]

        def size(self, name):
            if name.endswith("cloud"):
                raise ValueError()
            return 10**15 if name.endswith("large") else 100

    reports = []
    jobs, errors, skipped = runner.discover(
        Catalog(),
        HARDWARE,
        [{"id": "cpu"}],
        runner.BenchmarkSettings(state_dir=tmp_path),
        reports.append,
    )
    assert [job["model"] for job in jobs] == ["gemma3:small"]
    assert len(errors) == 2 and skipped == 1 and len(reports) == 3
    assert not runner.fits(100, HARDWARE, {"id": "gpu", "free": 1}, runner.Settings())


def test_isolated_storage_identity(tmp_path):
    first = runner.BenchmarkSettings(state_dir=tmp_path / "one")
    second = runner.BenchmarkSettings(state_dir=tmp_path / "two")
    assert first.container_name != second.container_name
    assert first.container_name != runner.Settings().container_name


def test_upload_batches_without_vouchers_and_never_replays_uncertain_results(tmp_path):
    state = {"results": [{"status": "completed", "entry": {"model": str(i)}} for i in range(23)]}
    requests = []

    def send(policy, method, path, body):
        requests.append(body)
        # Intent must already be durable before the network operation.
        saved = json.loads((tmp_path / "status.json").read_text())
        assert any(row.get("upload") == "uncertain" for row in saved["results"])
        if len(requests) == 1:
            raise TimeoutError()

    runner.upload(state, tmp_path, None, send, pause=lambda seconds: None)
    assert [len(request["entries"]) for request in requests] == [10, 10, 3]
    assert state["results"][0]["upload"] == "uncertain"
    assert state["results"][10]["upload"] == "submitted-for-moderation"
    assert state["results"][20]["upload"] == "submitted-for-moderation"
    runner.upload(state, tmp_path, None, send)
    assert all("voucher" not in request for request in requests)
    assert len(requests) == 3


def test_evaluation_reuses_six_cases_without_stress(monkeypatch, tmp_path):
    calls = []

    class Client:
        target = {"id": "cpu", "vendor": "cpu"}

        def start(self, **kwargs):
            calls.append("start")

        def pull(self, model, progress):
            calls.append("pull")

    class Pool:
        def __init__(self, *args):
            pass

        def client(self, *args, **kwargs):
            return Client()

        def load(self, *args):
            calls.append("load")

    async def measure(*args, **kwargs):
        assert kwargs["policy"] is None
        return local_result()

    monkeypatch.setattr(runner, "Runtimes", Pool)
    monkeypatch.setattr(runner, "snapshot", lambda: HARDWARE)
    monkeypatch.setattr(runner, "measure", measure)
    runner.evaluate(
        runner.BenchmarkSettings(state_dir=tmp_path),
        {"model": "gemma3:270m", "target": "cpu", "size": 10},
        tmp_path,
    )
    result = json.loads((tmp_path / "worker-result.json").read_text())
    assert result["status"] == "completed" and len(result["entry"]["cases"]) == 6
    assert "stress" not in result["entry"]
    assert "private" not in json.dumps(result)
    assert calls == ["start", "pull", "load"]


def test_run_recovers_after_failure_and_resumes(monkeypatch, tmp_path):
    class Pool:
        def __init__(self, *args):
            self.targets = {"cpu": {"id": "cpu", "supported": True}}

    monkeypatch.setattr(runner, "Runtimes", Pool)
    monkeypatch.setattr(runner, "snapshot", lambda: HARDWARE)
    jobs = [{"model": name, "target": "cpu", "size": 1} for name in ["bad:small", "good:small"]]
    monkeypatch.setattr(runner, "discover", lambda *args: (jobs, [], 0))
    calls = []

    def bounded(function, args, timeout):
        settings, job, folder = args
        calls.append((function.__name__, job["model"]))
        if function == runner.cleanup:
            runner.write_json(folder / "cleanup-result.json", {"ok": True})
        elif job["model"] == "good:small":
            runner.write_json(folder / "worker-result.json", {"status": "completed", "entry": {}})
        return True

    monkeypatch.setattr(runner, "bounded", bounded)
    monkeypatch.setattr(runner, "exchange", lambda *args: {})
    monkeypatch.setattr(runner, "upload", lambda *args: None)
    args = options(tmp_path)
    assert runner.run(args, tmp_path) == 0
    state = json.loads((tmp_path / "status.json").read_text())
    assert [row["status"] for row in state["results"]] == ["failed", "completed"]
    assert all(row["cleaned"] for row in state["results"])
    assert calls == [
        ("evaluate", "bad:small"),
        ("cleanup", "bad:small"),
        ("evaluate", "good:small"),
        ("cleanup", "good:small"),
    ]
    assert runner.run(args, tmp_path) == 0
    assert len(calls) == 4


def test_cleanup_deletes_only_evaluated_model(monkeypatch, tmp_path):
    calls = []

    class Client:
        def stop(self):
            calls.append("stop")

        def start(self):
            calls.append("start")

        def api(self, method, path, body=None, **kwargs):
            if method == "GET":
                return {"models": [{"name": "gemma3:270m"}, {"name": "other:latest"}]}
            calls.append(body["model"])
            return {}

    monkeypatch.setattr(
        runner, "Runtimes", lambda *args: SimpleNamespace(client=lambda *a, **k: Client())
    )
    runner.cleanup(None, {"model": "gemma3:270m", "target": "cpu"}, tmp_path)
    assert calls == ["stop", "start", "gemma3:270m", "stop"]
    assert json.loads((tmp_path / "cleanup-result.json").read_text())["ok"]


def test_interrupted_cleanup_blocks_new_downloads(monkeypatch, tmp_path):
    args = options(tmp_path)
    monkeypatch.setattr(runner, "snapshot", lambda: HARDWARE)
    monkeypatch.setattr(
        runner,
        "Runtimes",
        lambda *a: SimpleNamespace(targets={"cpu": {"id": "cpu", "supported": True}}),
    )
    monkeypatch.setattr(runner, "discover", lambda *a: ([], [], 0))
    monkeypatch.setattr(runner, "upload", lambda *a: None)
    runner.run(args, tmp_path)
    state = json.loads((tmp_path / "status.json").read_text())
    state["active"] = {"model": "gemma3:270m", "target": "cpu", "size": 1}
    runner.write_json(tmp_path / "status.json", state)
    # A stale success file must not make a new failed cleanup appear successful.
    runner.write_json(tmp_path / "cleanup-result.json", {"ok": True})
    calls = []
    monkeypatch.setattr(runner, "bounded", lambda function, *a: calls.append(function))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runner.run(args, tmp_path)
    assert calls == [runner.cleanup]


def test_cli_requires_explicit_sharing_consent(capsys):
    with pytest.raises(SystemExit):
        runner.main(["start"])
    assert "--confirm-share" in capsys.readouterr().err


def test_status_without_run(tmp_path, capsys):
    assert runner.main(["status", "--run-dir", str(tmp_path)]) == 0
    assert not json.loads(capsys.readouterr().out)["running"]


def test_invalid_gpu_target_lists_supported_ids(monkeypatch, tmp_path):
    args = options(tmp_path)
    args.target = ["gpu0"]
    monkeypatch.setattr(
        runner,
        "Runtimes",
        lambda *a: SimpleNamespace(
            targets={
                "cpu": {"id": "cpu", "supported": True},
                "gpu-example": {"id": "gpu-example", "supported": True},
            }
        ),
    )
    with pytest.raises(runner.RunnerConfigurationError, match="cpu, gpu-example"):
        runner.run(args, tmp_path)


def test_list_gpus_is_read_only_and_needs_no_consent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        runner,
        "snapshot",
        lambda: {
            "gpus": [
                {"id": "GPU-example", "vendor": "nvidia", "name": "Example GPU"},
                {"id": "unsupported", "vendor": "other", "name": "Other GPU"},
            ]
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Listing must not create runtime storage or contact Docker/network")

    monkeypatch.setattr(runner.Runtimes, "__init__", forbidden)
    monkeypatch.setattr(runner, "inspect_server_certificate", forbidden)
    folder = tmp_path / "not-created"
    assert runner.main(["--list-gpus", "--run-dir", str(folder)]) == 0
    output = capsys.readouterr().out
    assert "gpu-" in output and "Example GPU" in output
    assert "supported" in output and "unsupported" in output
    assert not folder.exists()


def test_first_gpu_default_and_resume_selection():
    targets = {
        "cpu": {"id": "cpu", "supported": True},
        "gpu-unsupported": {"id": "gpu-unsupported", "supported": False},
        "gpu-first": {"id": "gpu-first", "supported": True},
        "gpu-second": {"id": "gpu-second", "supported": True},
    }
    assert [t["id"] for t in runner.select_targets(targets, None, {})] == ["gpu-first"]
    assert [t["id"] for t in runner.select_targets(targets, ["cpu"], {})] == ["cpu"]
    state = {"selected_targets": ["gpu-second"]}
    assert [t["id"] for t in runner.select_targets(targets, None, state)] == ["gpu-second"]
    legacy = {"jobs": [{"target": "cpu"}, {"target": "gpu-second"}]}
    assert [t["id"] for t in runner.select_targets(targets, None, legacy)] == ["cpu", "gpu-second"]
    with pytest.raises(runner.RunnerConfigurationError, match="No CPU fallback"):
        runner.select_targets({"cpu": targets["cpu"]}, None, {})
    with pytest.raises(runner.RunnerConfigurationError):
        runner.select_targets({"cpu": targets["cpu"]}, None, state)


def test_upload_automatically_establishes_trust_before_sending(monkeypatch, tmp_path):
    policy = runner.SharingSettings(enabled=True)
    calls = []
    monkeypatch.setattr(runner, "upload_policy", lambda p, f: calls.append("trust") or p)
    state = {"results": [{"status": "completed", "entry": {}}]}
    runner.upload(state, tmp_path, policy, send=lambda *args: calls.append("upload"))
    assert calls == ["trust", "upload"]


def test_trust_failure_does_not_mark_reports_uncertain(monkeypatch, tmp_path):
    def fail(*args):
        raise runner.RunnerConfigurationError("Certificate unavailable")

    monkeypatch.setattr(runner, "upload_policy", fail)
    state = {"results": [{"status": "completed", "entry": {}}]}
    with pytest.raises(runner.RunnerConfigurationError):
        runner.upload(state, tmp_path, runner.SharingSettings(enabled=True))
    assert "upload" not in state["results"][0]


def test_resume_mismatch_names_changed_options_without_altering_checkpoint(tmp_path):
    args = options(tmp_path)
    state = {
        "configuration": {
            "context": args.context,
            "destination": "https://127.0.0.1:9443",
            "families": args.family,
            "targets": ["gpu-old"],
            "rootful": args.allow_rootful_docker,
        },
        "results": [],
    }
    runner.write_json(tmp_path / "status.json", state)
    with pytest.raises(runner.RunnerConfigurationError, match=r"differs \(targets\)"):
        runner.run(args, tmp_path)
    assert json.loads((tmp_path / "status.json").read_text()) == state


def test_cli_displays_safe_configuration_reason(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner.os, "geteuid", lambda: 1000)

    def invalid(*args):
        raise runner.RunnerConfigurationError("Supported target IDs: cpu, gpu-example")

    monkeypatch.setattr(runner, "run", invalid)
    previous = runner.signal.getsignal(runner.signal.SIGTERM)
    old_mask = runner.os.umask(0o077)
    try:
        assert (
            runner.main(["start", "--foreground", "--confirm-share", "--run-dir", str(tmp_path)])
            == 1
        )
    finally:
        runner.signal.signal(runner.signal.SIGTERM, previous)
        runner.os.umask(old_mask)
    assert "Supported target IDs: cpu, gpu-example" in capsys.readouterr().err


def test_worker_deadline_terminates_process():
    started = time.monotonic()
    assert not runner.bounded(time.sleep, (30,), 0.1)
    assert time.monotonic() - started < 10


def test_background_launch_detaches_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.os, "geteuid", lambda: 1000)
    calls = []

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    old_mask = runner.os.umask(0o077)
    try:
        assert (
            runner.main(
                [
                    "start",
                    "--run-dir",
                    str(tmp_path / "run"),
                    "--confirm-share",
                ]
            )
            == 0
        )
    finally:
        runner.os.umask(old_mask)
    command, kwargs = calls[0]
    assert command[-1] == "--foreground"
    assert "a" * 43 not in " ".join(command)
    assert kwargs["start_new_session"] and kwargs["close_fds"]
