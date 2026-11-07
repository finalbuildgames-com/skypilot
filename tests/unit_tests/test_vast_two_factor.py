"""Vast: the two-factor session key, optional ssh key registration, and the direct ssh endpoint."""
from unittest import mock

import pytest
import requests

from sky import authentication
from sky.adaptors import vast as vast_adaptor
from sky.provision.vast import instance as vast_instance


def test_the_two_factor_session_key_wins_over_the_api_key(tmp_path,
                                                          monkeypatch):
    (tmp_path / '.config/vastai').mkdir(parents=True)
    (tmp_path / '.config/vastai/vast_api_key').write_text('apikey\n')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('VAST_API_KEY', raising=False)
    assert vast_adaptor.get_api_key() == 'apikey'
    (tmp_path / '.config/vastai/vast_tfa_key').write_text('tfakey\n')
    assert vast_adaptor.get_api_key() == 'tfakey'
    monkeypatch.setenv('VAST_API_KEY', 'envkey')
    assert vast_adaptor.get_api_key() == 'envkey'


def test_the_cluster_gets_the_session_key_under_the_usual_name(
        tmp_path, monkeypatch):
    from sky.clouds import vast as vast_cloud
    monkeypatch.setenv('HOME', str(tmp_path))
    (tmp_path / '.config/vastai').mkdir(parents=True)
    assert vast_cloud.Vast().get_credential_file_mounts() == {
        '~/.config/vastai/vast_api_key': '~/.config/vastai/vast_api_key'
    }
    (tmp_path / '.config/vastai/vast_tfa_key').write_text('tfakey')
    assert vast_cloud.Vast().get_credential_file_mounts() == {
        '~/.config/vastai/vast_api_key': '~/.config/vastai/vast_tfa_key'
    }


def _http_error(status, text=''):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode()  # pylint: disable=protected-access
    return requests.HTTPError(response=response)


def _register(tmp_path, error):
    sdk = mock.MagicMock()
    sdk.show_ssh_keys.return_value = []
    sdk.create_ssh_key.side_effect = error
    pub = tmp_path / 'k.pub'
    pub.write_text('ssh-rsa AAAA test')
    with mock.patch.object(authentication.vast, 'vast', return_value=sdk), \
            mock.patch.object(authentication.auth_utils,
                              'get_or_generate_keys',
                              return_value=('priv', str(pub))), \
            mock.patch.object(authentication, 'configure_ssh_info',
                              side_effect=lambda c: c):
        return authentication.setup_vast_authentication({'auth': {}}), pub


@pytest.mark.parametrize('status', [401, 403])
def test_a_key_that_cannot_register_ssh_keys_still_launches(tmp_path, status):
    out, pub = _register(tmp_path, _http_error(status))
    assert out['auth']['ssh_public_key'] == str(pub)


def test_other_errors_of_the_registration_still_fail(tmp_path):
    with pytest.raises(requests.HTTPError):
        _register(tmp_path, _http_error(500, 'boom'))


def _running(**kw):
    d = {
        'id': '9',
        'status': 'RUNNING',
        'name': 'c-head',
        'local_ipaddrs': '172.17.0.2 ',
        'ssh_host': 'ssh3.vast.ai',
        'ssh_port': 10822,
        'public_ipaddr': '15.1.2.3\n',
        'ports': {}
    }
    d.update(kw)
    return d


def _endpoint(inst):
    with mock.patch.object(vast_instance,
                           '_filter_instances',
                           return_value={'9': inst}):
        info = vast_instance.get_cluster_info('r', 'c', {})
    node = info.instances['9'][0]
    return node.external_ip, node.ssh_port


def test_a_gateway_instance_is_reached_on_the_gateway():
    assert _endpoint(_running()) == ('ssh3.vast.ai', 10822)


def test_a_direct_instance_is_reached_on_its_own_address():
    ports = {'22/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '45239'}]}
    assert _endpoint(_running(ports=ports)) == ('15.1.2.3', 45239)
