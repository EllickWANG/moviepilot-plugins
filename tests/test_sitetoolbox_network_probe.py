"""验证固定目标探针的密钥隔离、环境代理排除及 DNS 连接边界。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

spec = importlib.util.spec_from_file_location('network_probe_test', Path(__file__).parents[1] / 'plugins.v2/sitetoolbox/network_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_proxy_report_does_not_include_credentials():
    result = probe.safe_proxy('http://alice:super-secret@proxy.example:7890/path?token=hidden')
    assert result == {'scheme': 'http', 'host': 'proxy.example', 'port': 7890}


def test_profile_response_omits_sensitive_fields_and_messages():
    body = json.dumps({'code': '0', 'message': 'secret message', 'data':
                       {'username': 'Nyxara', 'token': 'secret-token', 'passkey': 'secret-passkey'}}).encode()
    result = probe._summary(200, {'Server': 'cloudflare', 'Set-Cookie': 'secret-cookie'}, body)
    assert result['json'] == {'code': '0', 'has_user': True}
    assert 'secret' not in json.dumps(result)
    assert 'Set-Cookie' not in result['headers']


def test_only_mteam_host_overrides_are_reported():
    entries = probe.hosts_overrides('127.0.0.1 private.local\n1.2.3.4 api.m-team.cc unrelated.local # comment\n')
    assert entries == [{'address': '1.2.3.4', 'names': ['api.m-team.cc']}]


@pytest.mark.parametrize('mode', ['direct', 'configured'])
def test_isolated_requests_bypass_environment_and_keep_key_out_of_report(mode):
    response = Mock()
    response.status_code = 403
    response.headers = {'Server': 'nginx'}
    response.raw._connection = None
    response.raw.read.return_value = b'<title>403 Forbidden</title>'
    session = Mock()
    session.post.return_value = response
    proxies = {'http': 'http://proxy.example:7890', 'https': 'http://proxy.example:7890'}
    with patch.object(probe.requests, 'Session', return_value=session):
        result = probe._request('api.m-team.cc', mode, {'x-api-key': 'test-secret'}, proxies, Mock())
    assert session.trust_env is False
    assert session.post.call_args.kwargs['proxies'] == (proxies if mode == 'configured' else {})
    assert result['status'] == 403
    assert 'test-secret' not in json.dumps(result)
    session.close.assert_called_once()
    response.close.assert_called_once()


def test_errors_never_echo_proxy_password_or_api_key():
    session = Mock()
    session.post.side_effect = probe.requests.exceptions.ProxyError('http://alice:password@proxy/api?key=secret')
    with patch.object(probe.requests, 'Session', return_value=session):
        result = probe._request('api.m-team.cc', 'configured', {}, {}, Mock())
    assert result['error'] == 'ProxyError'
    assert result['proxy_error'] is True
    assert 'password' not in json.dumps(result)
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('host,address', [('api.m-team.cc', '127.0.0.1'), ('evil.example', '8.8.8.8')])
def test_pinned_requests_reject_private_addresses_and_arbitrary_hosts(host, address):
    with patch.object(probe.urllib3, 'HTTPSConnectionPool') as connection:
        assert probe._pinned(host, address, {})['error'] == 'InvalidDestination'
    connection.assert_not_called()


def test_dns_pin_preserves_sni_hostname_and_certificate_validation():
    pool = Mock()
    response = Mock(status=200, headers={})
    response.read.return_value = b'{"code":"0","data":{"username":"Nyxara"}}'
    pool.request.return_value = response
    with patch.object(probe.urllib3, 'HTTPSConnectionPool', return_value=pool) as connect:
        result = probe._pinned('api.m-team.cc', '104.26.11.251', {'x-api-key': 'secret'})
    assert connect.call_args.kwargs['server_hostname'] == 'api.m-team.cc'
    assert connect.call_args.kwargs['assert_hostname'] == 'api.m-team.cc'
    assert connect.call_args.kwargs['cert_reqs'] == 'CERT_REQUIRED'
    assert result['json']['has_user'] is True
    assert 'secret' not in json.dumps(result)
    pool.close.assert_called_once()
