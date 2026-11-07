"""Vast: launch an offer the caller picked (`offer_id`) and cap the hourly price (`max_dph`)."""
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import vast_catalog
from sky.provision.vast import utils as vast_utils


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
    assert _launch(sdk, {
        'offer_id': 4242,
        'max_dph': 0.1,
        'label': 'my-label'
    }) == 77
    sdk.search_offers.assert_not_called()
    sent = sdk.create_instance.call_args.kwargs
    assert sent['id'] == 4242
    assert sent['label'] == 'my-label'
    assert 'offer_id' not in sent and 'max_dph' not in sent


def test_the_search_skips_offers_over_the_cap_and_takes_the_cheapest_left():
    sdk = _sdk(offers=[{
        'id': 1,
        'dph_total': 1.35
    }, {
        'id': 2,
        'dph_total': 0.09
    }, {
        'id': 3,
        'dph_total': 0.06
    }])
    assert _launch(sdk, {'max_dph': 0.1}) == 77
    assert sdk.create_instance.call_args.kwargs['id'] == 3


def test_no_offer_under_the_cap_is_an_error():
    sdk = _sdk(offers=[{'id': 1, 'dph_total': 1.35}])
    with pytest.raises(RuntimeError, match=r'costs 0.3\$/h or less'):
        _launch(sdk, {'max_dph': 0.3})
    sdk.create_instance.assert_not_called()


def test_without_a_cap_the_first_search_result_is_rented():
    sdk = _sdk(offers=[{'id': 1, 'dph_total': 1.35}], price=1.35)
    assert _launch(sdk) == 77
    assert sdk.create_instance.call_args.kwargs['id'] == 1
    sdk.destroy_instance.assert_not_called()


def test_an_instance_dearer_than_the_cap_is_destroyed():
    sdk = _sdk(price=0.5)
    with pytest.raises(RuntimeError, match='over the cap'):
        _launch(sdk, {'offer_id': 5, 'max_dph': 0.1})
    sdk.destroy_instance.assert_called_once_with(id=77)


_COLUMNS = [
    'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
    'MemoryGiB', 'GpuInfo', 'Price', 'SpotPrice', 'Region', 'HostingType'
]


def _row(itype):
    return [
        itype, 'GTX1070', 1, 14, 7.8, "{'Gpus': []}", 0.048, 0.02,
        'South Korea, KR', 0
    ]


def test_pinned_offers_are_in_the_catalog(tmp_path):
    hosted = pd.DataFrame([_row('1x-RTX_3060_Ti-32-65536')], columns=_COLUMNS)
    pinned = tmp_path / 'pinned.csv'
    pd.DataFrame([_row('1x-GTX_1070-14-8007')],
                 columns=_COLUMNS).to_csv(pinned, index=False)
    lazy = mock.MagicMock()
    lazy._load_df.return_value = hosted  # pylint: disable=protected-access
    with mock.patch.object(vast_catalog, '_hosted_df', lazy), \
            mock.patch.object(vast_catalog.common, 'get_catalog_path',
                              return_value=str(pinned)):
        assert vast_catalog.instance_type_exists('1x-GTX_1070-14-8007')
        assert vast_catalog.instance_type_exists('1x-RTX_3060_Ti-32-65536')
        assert vast_catalog.get_hourly_cost(
            '1x-GTX_1070-14-8007') == pytest.approx(0.048)
        assert vast_catalog.get_accelerators_from_instance_type(
            '1x-GTX_1070-14-8007') == {
                'GTX1070': 1
            }
    with mock.patch.object(vast_catalog, '_hosted_df', lazy), \
            mock.patch.object(vast_catalog.common, 'get_catalog_path',
                              return_value=str(tmp_path / 'none.csv')):
        assert not vast_catalog.instance_type_exists('1x-GTX_1070-14-8007')
