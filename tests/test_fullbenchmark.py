"""Offline full-benchmark discovery, recovery, upload safety, and CLI regressions."""

import json
import time
from types import SimpleNamespace

import pytest
from test_benchmark_exchange import local_result
from test_core import HARDWARE

import RunFullbenchmark as runner


def options(tmp_path):
    vouchers = tmp_path / "vouchers.json"
    vouchers.write_text(json.dumps(["a" * 43]))
    return SimpleNamespace(
        context=4096,
        server="127.0.0.1",
        port=9443,
        ca_cert=None,
        vouchers_file=vouchers,
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


def test_upload_batches_and_never_replays_uncertain_vouchers(tmp_path):
    state = {"results": [{"status": "completed", "entry": {"model": str(i)}} for i in range(23)]}
    requests = []

    def send(policy, method, path, body):
        requests.append(body)
        # Intent must already be durable before the network operation.
        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["vouchers_used"] == len(requests)
        if len(requests) == 1:
            raise TimeoutError()

    runner.upload(state, tmp_path, None, ["a", "b"], send, pause=lambda seconds: None)
    assert [len(request["entries"]) for request in requests] == [10, 10]
    assert state["results"][0]["upload"] == "uncertain"
    assert state["results"][10]["upload"] == "submitted-for-moderation"
    assert "upload" not in state["results"][20]
    runner.upload(state, tmp_path, None, ["a", "b", "c"], send)
    assert requests[-1]["voucher"] == "c" and len(requests[-1]["entries"]) == 3
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
    args.vouchers_file.write_text(json.dumps(["b" * 43]))
    with pytest.raises(ValueError, match="append"):
        runner.run(args, tmp_path)


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


def test_worker_deadline_terminates_process():
    started = time.monotonic()
    assert not runner.bounded(time.sleep, (30,), 0.1)
    assert time.monotonic() - started < 10


def test_background_launch_detaches_without_secrets(monkeypatch, tmp_path):
    args = options(tmp_path)
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
                    "--vouchers-file",
                    str(args.vouchers_file),
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
