from unittest.mock import Mock

import pytest

from scripts import rootless_nvidia as setup


def test_check_requests_each_uuid_and_rejects_extra_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "rootless_info", Mock())
    monkeypatch.setattr(setup, "run", Mock(return_value="GPU 0 (UUID: GPU-123)\nGPU 1 (UUID: GPU-456)"))
    docker = Mock(side_effect=["GPU 0 (UUID: GPU-123)", "GPU 0 (UUID: GPU-456)"])
    monkeypatch.setattr(setup, "docker", docker)

    setup.check(tmp_path, "ubuntu:24.04", None)
    for call, gpu in zip(docker.call_args_list, ("GPU-123", "GPU-456"), strict=True):
        args = call.args
        assert args[args.index("--gpus") + 1] == f"device={gpu}"
        assert "--pull=never" in args and "--network=none" in args and "--rm" in args

    docker.side_effect = None
    docker.return_value = "GPU-123\nGPU-456"
    with pytest.raises(ValueError, match="exactly the requested GPU"):
        setup.check(tmp_path, "ubuntu:24.04", "GPU-123")


def test_mirror_waits_for_spec_and_updates_without_rewriting_identical_copy(tmp_path, monkeypatch):
    host = Mock(side_effect=[None, b"first", b"first", b"second"])
    monkeypatch.setattr(setup, "read_host_spec", host)
    dest = tmp_path / "cdi/nvidia.yaml"
    setup.mirror(tmp_path)
    assert not dest.exists()
    setup.mirror(tmp_path)
    original = dest.stat()
    assert dest.read_bytes() == b"first"
    assert original.st_mode & 0o777 == 0o600
    setup.mirror(tmp_path)
    assert dest.stat().st_ino == original.st_ino
    setup.mirror(tmp_path)
    assert dest.read_bytes() == b"second"
    assert list(dest.parent.iterdir()) == [dest]


def test_installer_writes_units_without_restarting_docker(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    config = tmp_path / "config"
    monkeypatch.setattr(setup, "rootless_info", Mock(return_value={"CDISpecDirs": [str(runtime / "cdi")]}))
    monkeypatch.setattr(setup, "read_host_spec", Mock(return_value=b"spec"))
    run = Mock(return_value="active\n")
    monkeypatch.setattr(setup, "run", run)

    units = config / "systemd/user"
    (runtime / "cdi").mkdir(parents=True)
    dest = runtime / "cdi/nvidia.yaml"
    dest.write_text("existing spec")
    with pytest.raises(ValueError, match="take over"):
        setup.install(runtime, config)
    assert not units.exists()
    dest.unlink()
    run.reset_mock()

    setup.install(runtime, config)
    setup.install(runtime, config)  # Idempotent.
    assert f"After={setup.SERVICE}" in (units / "docker.service.d" / setup.DROP_IN).read_text()
    assert f"PathChanged={setup.SOURCE}" in (units / setup.WATCHER).read_text()
    assert (config / "llameros" / setup.HELPER).is_file()
    assert [call.args[2:] for call in run.call_args_list[:4]] == [
        ("is-active", "docker.service"),
        ("daemon-reload",),
        ("enable", "--now", setup.WATCHER),
        ("start", setup.SERVICE),
    ]


def test_install_requires_docker_to_search_user_runtime_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "rootless_info", Mock(return_value={"CDISpecDirs": ["/run/cdi"]}))
    with pytest.raises(ValueError, match="does not list"):
        setup.install(tmp_path / "run", tmp_path / "config")
    assert not (tmp_path / "config").exists()