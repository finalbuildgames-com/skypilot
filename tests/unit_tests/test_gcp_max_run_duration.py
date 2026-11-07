"""GCP: `gcp.max_run_duration_seconds`, a hard deadline Compute Engine itself enforces."""
import os

import jinja2
import yaml

import sky


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
    path = os.path.join(os.path.dirname(sky.__file__), 'templates', 'gcp-ray.yml.j2')
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
