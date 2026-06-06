"""Tests for scripts/config.py."""
import os

import pytest

import config as config_module
from config import Config, generate_redshift_password


@pytest.fixture
def cfg():
    """Load the real project config.yaml — keeps tests honest about what defaults ship."""
    return Config()


def test_loads_config_yaml(cfg):
    assert cfg.get('aws.region') == 'us-east-1'
    assert cfg.get('redshift.cluster_identifier') == 'sales-demo-cluster'


def test_publicly_accessible_is_off_by_default(cfg):
    """Regression: shipping config must not expose the cluster to the internet."""
    assert cfg.get('redshift.publicly_accessible') is False


def test_no_master_password_in_config_yaml(cfg):
    """Regression: master_password must NOT be in the committed config.yaml."""
    assert cfg.get('redshift.master_password') is None


def test_get_master_password_raises_without_env(monkeypatch, cfg):
    monkeypatch.delenv('MASTER_PASSWORD', raising=False)
    with pytest.raises(RuntimeError, match='MASTER_PASSWORD'):
        cfg.get_master_password()


def test_get_master_password_uses_env(monkeypatch, cfg):
    monkeypatch.setenv('MASTER_PASSWORD', 'EnvPassword1!')
    assert cfg.get_master_password() == 'EnvPassword1!'


def test_get_master_password_generate_fallback(monkeypatch, cfg):
    monkeypatch.delenv('MASTER_PASSWORD', raising=False)
    pw = cfg.get_master_password(generate_if_missing=True)
    assert 8 <= len(pw) <= 64
    assert any(c.isupper() for c in pw)
    assert any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw)


def test_env_overrides_take_precedence(monkeypatch, cfg):
    monkeypatch.setenv('AWS_REGION', 'eu-west-1')
    monkeypatch.setenv('CLUSTER_IDENTIFIER', 'override-cluster')
    monkeypatch.setenv('DATABASE_NAME', 'override-db')
    assert cfg.get_aws_region() == 'eu-west-1'
    assert cfg.get_cluster_identifier() == 'override-cluster'
    assert cfg.get_database_name() == 'override-db'


def test_dot_notation_returns_default_for_missing_key(cfg):
    assert cfg.get('does.not.exist', default='sentinel') == 'sentinel'


class TestGenerateRedshiftPassword:
    def test_default_length(self):
        pw = generate_redshift_password()
        assert len(pw) == 24

    def test_custom_length(self):
        pw = generate_redshift_password(length=32)
        assert len(pw) == 32

    def test_minimum_length_enforced(self):
        with pytest.raises(ValueError):
            generate_redshift_password(length=4)

    def test_meets_redshift_complexity(self):
        for _ in range(20):
            pw = generate_redshift_password()
            assert any(c.islower() for c in pw)
            assert any(c.isupper() for c in pw)
            assert any(c.isdigit() for c in pw)
            for forbidden in ('/', '@', '"', "'", ' ', '\\'):
                assert forbidden not in pw, f"forbidden char {forbidden!r} in {pw!r}"
