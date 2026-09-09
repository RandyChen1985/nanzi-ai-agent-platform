import pytest


pytestmark = pytest.mark.no_infrastructure


def test_docker_policy_is_available_when_platform_runs_in_container(monkeypatch):
    from app.services import config_service

    monkeypatch.setattr(config_service, "get_env", lambda: "docker")

    assert config_service.resolve_effective_sandbox_policy("docker") == "docker"
    assert config_service.resolve_effective_sandbox_policy("k8s") == "k8s"
    assert config_service.resolve_effective_sandbox_policy("e2b") == "e2b"
    assert config_service.resolve_effective_sandbox_policy("local") == "local"


def test_docker_policy_remains_available_on_host(monkeypatch):
    from app.services import config_service

    monkeypatch.setattr(config_service, "get_env", lambda: "host")

    assert config_service.resolve_effective_sandbox_policy("docker") == "docker"
    assert config_service.resolve_effective_sandbox_policy("k8s") == "k8s"


def test_config_update_allows_docker_policy_inside_container(monkeypatch):
    from app.services import config_service

    monkeypatch.setattr(config_service, "get_env", lambda: "docker")

    # 挂载 /var/run/docker.sock 后不再抛出 ValueError
    config_service.validate_config_update("sandbox_policy", "docker")
    config_service.validate_config_update("sandbox_policy", "k8s")
    config_service.validate_config_update("sandbox_policy", "local")
    config_service.validate_config_update("sandbox_policy", "e2b")
