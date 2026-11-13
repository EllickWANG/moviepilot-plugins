"""隔离插件存储及站点依赖，验证核心采集结果的同步、回退和历史兼容。"""
import importlib.util
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = {"id": 11, "name": "高清杜比", "domain": "uhddolby.com",
        "url": "https://www.uhddolby.com/", "is_active": True, "schema": "HDDolby"}
TODAY = datetime.now().strftime("%Y-%m-%d")


def userdata(**changes):
    payload = {"domain": SITE["domain"], "username": "Nyxara", "userid": "45476",
               "user_level": "User", "upload": 1182202144756, "download": 47243562853,
               "ratio": 25.02, "bonus": 63475, "seeding": 2,
               "seeding_size": 30526657312, "err_msg": "",
               "updated_day": TODAY, "updated_time": "14:02:12"}
    return dict(payload, **changes)


def load_plugin(monkeypatch, plugin_dir):
    """只替换框架边界，实际执行完整插件源码及原有页面渲染。"""
    class Storage:
        def __init__(self):
            self.storage = {}
            self.messages = []

        def get_data(self, key):
            return deepcopy(self.storage.get(key))

        def save_data(self, key, value):
            self.storage[key] = deepcopy(value)

        def post_message(self, **message):
            self.messages.append(message)

    refresh = Mock(return_value=SimpleNamespace(model_dump=lambda: userdata()))
    latest = Mock(return_value=[])
    indexers = Mock(return_value=[deepcopy(SITE)])

    def domain(url):
        hostname = urlsplit(url if "://" in url else "https://" + url).hostname or ""
        return hostname.removeprefix("www.")

    modules = {
        "app.chain.site": {"SiteChain": lambda: SimpleNamespace(refresh_userdata=refresh)},
        "app.core.config": {"settings": SimpleNamespace(TZ="Asia/Shanghai")},
        "app.core.event": {"eventmanager": SimpleNamespace(register=lambda _: lambda fn: fn)},
        "app.db.site_oper": {"SiteOper": lambda: SimpleNamespace(get_userdata_latest=latest)},
        "app.helper.sites": {"SitesHelper": lambda: SimpleNamespace(get_indexers=indexers)},
        "app.log": {"logger": Mock()},
        "app.plugins": {"_PluginBase": Storage},
        "app.schemas.types": {"EventType": SimpleNamespace(SiteRefreshed="site.refreshed"),
                              "NotificationType": SimpleNamespace(SiteMessage="site.message")},
        "app.utils.string": {"StringUtils": SimpleNamespace(get_url_domain=domain,
                                                            str_filesize=lambda value: str(value))},
    }
    for name, attrs in modules.items():
        parts = name.split(".")
        for length in range(1, len(parts)):
            parent = ".".join(parts[:length])
            if parent not in modules:
                mod = ModuleType(parent)
                mod.__path__ = []
                monkeypatch.setitem(__import__("sys").modules, parent, mod)
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(__import__("sys").modules, name, mod)
    spec = importlib.util.spec_from_file_location("sitedatastat_test", ROOT / plugin_dir / "sitedatastat/__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = module.sitedatastat()
    return SimpleNamespace(plugin=plugin, module=module, refresh=refresh, latest=latest, indexers=indexers)


@pytest.fixture(params=["plugins", "plugins.v2"])
def env(monkeypatch, request):
    return load_plugin(monkeypatch, request.param)


def rows(*payloads):
    return [SimpleNamespace(to_dict=lambda p=p: deepcopy(p)) for p in payloads]


def seed_history(plugin):
    plugin.save_data("2020-01-01", {SITE["domain"]: userdata(upload=99)})
    plugin.save_data("_dates", ["2020-01-01"])


def test_init_syncs_existing_core_data_and_preserves_history(env):
    seed_history(env.plugin)
    before = deepcopy(env.plugin.storage["2020-01-01"])
    env.latest.return_value = rows(userdata())
    env.plugin.init_plugin({"enabled": True})
    current = env.plugin.get_data(TODAY)[SITE["domain"]]
    assert current["upload"] == 1182202144756
    assert current["seeding"] == 2
    assert current["updated_at"] == TODAY + " 14:02:12"
    assert env.plugin.get_data("2020-01-01") == before
    env.refresh.assert_not_called()


def test_core_event_syncs_without_fetching_or_sending_notifications(env):
    env.plugin._enabled = True
    env.latest.return_value = rows(userdata())
    env.plugin.on_site_refreshed(SimpleNamespace(event_data={"site_id": 11}))
    assert env.plugin.get_data(TODAY)[SITE["domain"]]["username"] == "Nyxara"
    assert env.plugin.messages == []
    env.refresh.assert_not_called()


def test_disabled_plugin_ignores_events(env):
    env.plugin.on_site_refreshed(SimpleNamespace(event_data={"site_id": 11}))
    env.latest.assert_not_called()
    assert not env.plugin.storage


def test_collect_uses_core_and_accepts_real_zero_seeding(env):
    seed_history(env.plugin)
    env.refresh.return_value = SimpleNamespace(model_dump=lambda: userdata(seeding=0, seeding_size=0))
    env.plugin.collect()
    env.refresh.assert_called_once_with(site=SITE)
    current = env.plugin.get_data(TODAY)[SITE["domain"]]
    assert current["seeding"] == 0
    assert current["seeding_size"] == 0
    assert current["upload"] == 1182202144756
    assert current["err_msg"] is None


@pytest.mark.parametrize("reply", [None, {}, {"err_msg": "API unavailable"}])
def test_empty_or_failed_core_result_retries_and_falls_back_to_core_cache(env, reply):
    seed_history(env.plugin)
    env.latest.return_value = rows(userdata(updated_day="2026-11-12", updated_time="22:00:58"))
    env.refresh.return_value = None if reply is None else SimpleNamespace(model_dump=lambda: reply)
    env.plugin.collect()
    assert env.refresh.call_count == 2
    current = env.plugin.get_data(TODAY)[SITE["domain"]]
    assert current["upload"] == 1182202144756
    assert current["updated_at"] == "2026-11-12 22:00:58"
    assert current["err_msg"]


def test_exception_falls_back_to_plugin_history_when_core_has_none(env):
    seed_history(env.plugin)
    env.plugin._retry = False
    env.refresh.side_effect = RuntimeError("network down")
    env.plugin.collect()
    env.refresh.assert_called_once()
    current = env.plugin.get_data(TODAY)[SITE["domain"]]
    assert current["upload"] == 99
    assert current["err_msg"] == "network down"


def test_successful_retry_uses_fresh_data(env):
    env.refresh.side_effect = [RuntimeError("transient"), SimpleNamespace(model_dump=lambda: userdata())]
    env.plugin.collect()
    assert env.refresh.call_count == 2
    assert not env.plugin.get_data(TODAY)[SITE["domain"]]["err_msg"]


def test_new_account_can_have_zero_traffic(env):
    env.refresh.return_value = SimpleNamespace(model_dump=lambda: userdata(upload=0, download=0, seeding=0))
    env.plugin.collect()
    assert env.plugin.get_data(TODAY)[SITE["domain"]]["upload"] == 0
    assert not env.plugin.get_data(TODAY)[SITE["domain"]]["err_msg"]


def test_core_snapshot_uses_latest_valid_row_and_filters_unselected_sites(env):
    env.plugin._site_ids = [11]
    env.indexers.return_value += [dict(SITE, id=12, domain="other.test"), dict(SITE, id=13, is_active=False)]
    env.latest.return_value = rows(userdata(upload=1, updated_time="10:00:00"),
                                  userdata(upload=20, updated_time="12:00:00"),
                                  userdata(upload=30, updated_time="13:00:00", err_msg="failed"),
                                  userdata(domain="other.test"))
    env.plugin.sync_core_data()
    snapshot = env.plugin.get_data(TODAY)
    assert list(snapshot) == [SITE["domain"]]
    assert snapshot[SITE["domain"]]["upload"] == 20


def test_event_during_collect_does_not_deadlock_or_refetch(env):
    env.plugin._enabled = True

    def refresh(**_):
        env.plugin.on_site_refreshed(SimpleNamespace(event_data={"site_id": 11}))
        return SimpleNamespace(model_dump=lambda: userdata())

    env.refresh.side_effect = refresh
    env.plugin.collect()
    env.refresh.assert_called_once()
    assert env.plugin.get_data(TODAY)[SITE["domain"]]["seeding"] == 2


def test_no_core_data_does_not_erase_existing_history(env):
    seed_history(env.plugin)
    before = deepcopy(env.plugin.storage)
    env.plugin.sync_core_data()
    assert env.plugin.storage == before


def test_detail_page_keeps_table_charts_and_failure_reason(env):
    seed_history(env.plugin)
    env.latest.return_value = rows(userdata())
    env.refresh.side_effect = RuntimeError("network down")
    env.plugin.collect()
    page = str(env.plugin.get_page())
    assert "Nyxara" in page
    assert "1182202144756" in page
    assert "network down" in page
    assert "VApexChart" in page
    assert "2020-01-01" in page


def test_failed_refresh_event_cannot_clear_error_using_old_core_cache(env):
    env.latest.return_value = rows(userdata())
    env.refresh.side_effect = RuntimeError("network down")
    env.plugin.collect()
    env.plugin.sync_core_data()
    current = env.plugin.get_data(TODAY)[SITE["domain"]]
    assert current["err_msg"] == "network down"
    env.latest.return_value = rows(userdata(updated_time="15:00:00"))
    env.plugin.sync_core_data()
    assert not env.plugin.get_data(TODAY)[SITE["domain"]]["err_msg"]
