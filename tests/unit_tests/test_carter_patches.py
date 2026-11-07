"""Tests of the small patches Carter's fork carries (docs/design/RENTED.md section 26 of finalbuildgames-com/Carter):
Vast (two-factor key, pinned offers, price cap, label word, direct ssh endpoint, optional ssh key registration) and GCP (a hard deadline
for the VM). No cloud is called."""
import os
import textwrap
from unittest import mock

import jinja2
import pandas as pd
import pytest
import requests
import yaml

from sky import authentication
from sky.adaptors import vast as vast_adaptor
from sky.catalog import vast_catalog
from sky.provision.vast import instance as vast_instance
from sky.provision.vast import utils as vast_utils

# ---- adaptor: the key ----------------------------------------------------------------------------------------------


def test_the_two_factor_session_key_wins_over_the_api_key(tmp_path, monkeypatch):
    (tmp_path / '.config/vastai').mkdir(parents=True)
    (tmp_path / '.config/vastai/vast_api_key').write_text('apikey\n')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('VAST_API_KEY', raising=False)
    assert vast_adaptor.get_api_key() == 'apikey'
    (tmp_path / '.config/vastai/vast_tfa_key').write_text('tfakey\n')
    assert vast_adaptor.get_api_key() == 'tfakey'
    monkeypatch.setenv('VAST_API_KEY', 'envkey')
    assert vast_adaptor.get_api_key() == 'envkey'


def test_the_cluster_gets_the_session_key_under_the_usual_name(tmp_path, monkeypatch):
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


# ---- authentication: the ssh key registration is optional --------------------------------------------------------------


def _http_error(status, text=''):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode()  # pylint: disable=protected-access
    return requests.HTTPError(response=response)


@pytest.mark.parametrize('status', [401, 403])
def test_a_key_that_cannot_register_ssh_keys_still_launches(tmp_path, status):
    sdk = mock.MagicMock()
    sdk.show_ssh_keys.return_value = []
    sdk.create_ssh_key.side_effect = _http_error(status)
    pub = tmp_path / 'k.pub'
    pub.write_text('ssh-rsa AAAA test')
    with mock.patch.object(authentication.vast, 'vast', return_value=sdk), \
            mock.patch.object(authentication.auth_utils, 'get_or_generate_keys', return_value=('priv', str(pub))), \
            mock.patch.object(authentication, 'configure_ssh_info', side_effect=lambda c: c):
        out = authentication.setup_vast_authentication({'auth': {}})
    assert out['auth']['ssh_public_key'] == str(pub)


def test_other_errors_of_the_registration_still_fail(tmp_path):
    sdk = mock.MagicMock()
    sdk.show_ssh_keys.return_value = []
    sdk.create_ssh_key.side_effect = _http_error(500, 'boom')
    pub = tmp_path / 'k.pub'
    pub.write_text('ssh-rsa AAAA test')
    with mock.patch.object(authentication.vast, 'vast', return_value=sdk), \
            mock.patch.object(authentication.auth_utils, 'get_or_generate_keys', return_value=('priv', str(pub))):
        with pytest.raises(requests.HTTPError):
            authentication.setup_vast_authentication({'auth': {}})


# ---- provision/vast/utils: labels, offers, the price cap -------------------------------------------------------------


def test_a_label_may_carry_the_name_skypilot_finds_it_by():
    f = vast_utils.name_from_label
    assert f('mycluster-head') == 'mycluster-head'
    assert f('carter:host:abc:rented-1 exp=20261107T1200Z sky=rented-1-7fb3-head') == 'rented-1-7fb3-head'
    assert f('sky=x-head carter:gpu:-:a') == 'x-head'
    assert f('sky=') == 'sky='
    assert f(None) is None


def _sdk(offers=(), price=0.05):
    sdk = mock.MagicMock()
    sdk.search_offers.return_value = list(offers)
    sdk.create_instance.return_value = {'new_contract': 77}
    sdk.show_instance.return_value = {'id': 77, 'dph_total': price}
    sdk.client.api_key = 'key'
    return sdk


def _launch(sdk, kwargs=None, itype='1x-GTX_1070-14-8007'):
    with mock.patch.object(vast_utils.vast, 'vast', return_value=sdk):
        return vast_utils.launch(name='c-head',
                                 instance_type=itype,
                                 region='South Korea, KR',
                                 disk_size=40,
                                 image_name='img',
                                 ports=None,
                                 preemptible=False,
                                 secure_only=False,
                                 create_instance_kwargs=kwargs)


def test_a_pinned_offer_is_rented_without_a_search():
    sdk = _sdk()
    assert _launch(sdk, {'offer_id': 4242, 'max_dph': 0.1, 'label': 'L sky=c-head'}) == 77
    sdk.search_offers.assert_not_called()
    sent = sdk.create_instance.call_args.kwargs
    assert sent['id'] == 4242
    assert sent['label'] == 'L sky=c-head'
    assert 'offer_id' not in sent and 'max_dph' not in sent


def test_the_search_skips_offers_over_the_cap_and_takes_the_cheapest_left():
    sdk = _sdk(offers=[{'id': 1, 'dph_total': 1.35}, {'id': 2, 'dph_total': 0.09}, {'id': 3, 'dph_total': 0.06}])
    assert _launch(sdk, {'max_dph': 0.1}) == 77
    assert sdk.create_instance.call_args.kwargs['id'] == 3


def test_the_default_cap_is_thirty_cents():
    assert vast_utils.DEFAULT_MAX_DPH == 0.30
    sdk = _sdk(offers=[{'id': 1, 'dph_total': 1.35}])
    with pytest.raises(RuntimeError, match='costs 0.3\\$/h or less'):
        _launch(sdk)
    sdk.create_instance.assert_not_called()


def test_an_instance_dearer_than_the_cap_is_destroyed():
    sdk = _sdk(price=0.5)
    with pytest.raises(RuntimeError, match='over the cap'):
        _launch(sdk, {'offer_id': 5, 'max_dph': 0.1})
    sdk.destroy_instance.assert_called_once_with(id=77)


def test_the_instances_are_listed_by_the_name_in_their_label():
    sdk = mock.MagicMock()
    sdk.show_instances.return_value = [{
        'id': 9,
        'actual_status': 'running',
        'label': 'carter:gpu:-:x exp=20261107T1200Z sky=x-1-head'
    }]
    with mock.patch.object(vast_utils.vast, 'vast', return_value=sdk):
        got = vast_utils.list_instances()
    assert got['9']['name'] == 'x-1-head'
    with mock.patch.object(vast_instance.utils, 'list_instances', return_value=got):
        assert list(vast_instance._filter_instances('x-1', None)) == ['9']  # pylint: disable=protected-access


# ---- the catalog: pinned offers ---------------------------------------------------------------------------------------

_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs', 'MemoryGiB', 'GpuInfo', 'Price', 'SpotPrice', 'Region',
    'HostingType'
]


def _row(itype):
    return [itype, 'GTX1070', 1, 14, 7.8, "{'Gpus': []}", 0.048, 0.02, 'South Korea, KR', 0]


def test_pinned_offers_are_in_the_catalog(tmp_path):
    hosted = pd.DataFrame([_row('1x-RTX_3060_Ti-32-65536')], columns=_COLUMNS)
    pinned = tmp_path / 'pinned.csv'
    pd.DataFrame([_row('1x-GTX_1070-14-8007')], columns=_COLUMNS).to_csv(pinned, index=False)
    lazy = mock.MagicMock()
    lazy._load_df.return_value = hosted  # pylint: disable=protected-access
    with mock.patch.object(vast_catalog, '_hosted_df', lazy), \
            mock.patch.object(vast_catalog.common, 'get_catalog_path', return_value=str(pinned)):
        assert vast_catalog.instance_type_exists('1x-GTX_1070-14-8007')
        assert vast_catalog.instance_type_exists('1x-RTX_3060_Ti-32-65536')
        assert vast_catalog.get_hourly_cost('1x-GTX_1070-14-8007') == pytest.approx(0.048)
        assert vast_catalog.get_accelerators_from_instance_type('1x-GTX_1070-14-8007') == {'GTX1070': 1}
    with mock.patch.object(vast_catalog, '_hosted_df', lazy), \
            mock.patch.object(vast_catalog.common, 'get_catalog_path', return_value=str(tmp_path / 'none.csv')):
        assert not vast_catalog.instance_type_exists('1x-GTX_1070-14-8007')


# ---- provision/vast/instance: where ssh is -----------------------------------------------------------------------------


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
    with mock.patch.object(vast_instance, '_filter_instances', return_value={'9': inst}):
        info = vast_instance.get_cluster_info('r', 'c', {})
    node = info.instances['9'][0]
    return node.external_ip, node.ssh_port


def test_a_gateway_instance_is_reached_on_the_gateway():
    assert _endpoint(_running()) == ('ssh3.vast.ai', 10822)


def test_a_direct_instance_is_reached_on_its_own_address():
    ports = {'22/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '45239'}]}
    assert _endpoint(_running(ports=ports)) == ('15.1.2.3', 45239)


# ---- GCP: the hard deadline ------------------------------------------------------------------------------------------

_BASE = dict(cluster_name_on_cloud='c', region='us-central1', zones='us-central1-a', instance_type='c2d-highcpu-4', use_spot=False,
             gpu=None, gpu_count=None, disk_size=100, disk_tier='medium', disk_iops=None, image_id='projects/p/global/images/i',
             machine_image=None, labels={}, user_data=None, max_run_duration_seconds=None, gcp_project_id='p', credentials={},
             num_nodes=1, tpu_vm=False, tpu_type=None, tpu_node_name=None, gcp_queued_resource=None, runtime_version=None,
             gcp_use_managed_instance_group=False, gcp_use_managed_instance_group_value='0', specific_reservations=[], volumes=[],
             enable_gvnic=False, enable_gpu_direct=False, placement_policy=None, network_tier='premium', use_internal_ips=False,
             force_enable_external_ips=False, vpc_name=None, subnet_names=None, firewall_rule=None, docker_image=None,
             docker_container_name=None, docker_login_config=None, docker_run_options=[], provision_timeout=10, run_duration=None,
             ssh_max_sessions_config='', ssh_proxy_command=None, ssh_private_key='k', user='u', sky_local_path='a',
             sky_remote_path='b', sky_wheel_hash='c', sky_ray_yaml_local_path='d', sky_ray_yaml_remote_path='e', sky_pip_cmd='pip',
             initial_setup_commands=[], conda_installation_commands='', uv_installation_commands='',
             ray_skypilot_installation_commands='', copy_skypilot_templates_commands='')


def _scheduling(**over):
    path = os.path.join(os.path.dirname(authentication.__file__), 'templates', 'gcp-ray.yml.j2')
    with open(path, encoding='utf-8') as f:
        text = jinja2.Template(f.read()).render(**dict(_BASE, **over))
    cfg = yaml.safe_load(text)
    return cfg['available_node_types']['ray_head_default']['node_config'].get('scheduling')


def test_the_vm_gets_a_deadline_when_asked():
    assert _scheduling() is None
    assert _scheduling(max_run_duration_seconds=86400) == {
        'instanceTerminationAction': 'DELETE',
        'maxRunDuration': {
            'seconds': 86400
        }
    }
    spot = _scheduling(use_spot=True, max_run_duration_seconds=3600)
    assert spot['instanceTerminationAction'] == 'DELETE'
    assert spot['provisioningModel'] == 'SPOT'
    assert spot['maxRunDuration'] == {'seconds': 3600}
    assert _scheduling(use_spot=True).get('maxRunDuration') is None


def test_the_deadline_is_a_config_key_a_task_may_set():
    from sky.skylet import constants
    assert ('gcp', 'max_run_duration_seconds') in constants.OVERRIDEABLE_CONFIG_KEYS_IN_TASK
    from sky.utils import schemas
    props = schemas.get_config_schema()['properties']['gcp']['properties']
    assert props['max_run_duration_seconds']['type'] == 'integer'
