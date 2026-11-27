import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from test_flowbrush_diagnostics import PLUGIN

spec = importlib.util.spec_from_file_location('workspace_model', Path(__file__).resolve().parents[1]/'plugins.v2/flowbrush/workspace.py')
MODEL=importlib.util.module_from_spec(spec)
spec.loader.exec_module(MODEL)


class WorkspaceTest(TestCase):
    def setUp(self):
        self.raw={'hash':'one','name':'Private task','state':'stalledDL','progress':0,
                  'tags':'刷流, 站点/A','tracker':'https://private/?passkey=secret',
                  'magnet_uri':'magnet:secret','save_path':'/private/data','uploaded':42,
                  'upspeed':0,'dlspeed':0,'size':1000,'added_on':1}
        self.records={'one':{'site_name':'A','title':'Private task','time':1,'site_cookie':'secret-cookie'}}
        self.job={'status':'success','sites':{'A':{'added':1}}}
        self.plugin=SimpleNamespace(_brush_config=PLUGIN.BrushConfig({'enabled':True,'brushsites':[1],
            'downloader':'test','maxdlcount':8,'site_download_cap':2}),plugin_version='test',
            _brush_run_lock=threading.Lock(),get_data=lambda key:self.records if key=='torrents' else self.job,
            downloader=Mock())
        self.plugin.downloader.get_torrents.return_value=([self.raw],False)

    def snapshot(self):
        return MODEL.build_workspace(self.plugin,[{'id':1,'name':'A'},{'id':2,'name':'B'}],['test'])

    def test_workspace_is_read_only_and_redacts_credentials(self):
        view=self.snapshot()
        self.assertEqual(view['summary']['downloading'],1)
        self.assertEqual(view['tasks'][0]['status'],'waiting')
        self.assertEqual(view['sites'][0]['downloading'],1)
        self.assertEqual(view['sites'][1]['reason'],'已暂停新增，已有任务继续维护')
        for secret in ['passkey','secret-cookie','magnet:secret','/private/data']:
            self.assertNotIn(secret,str(view))
        self.assertEqual([c[0] for c in self.plugin.downloader.mock_calls],['get_torrents'])

    def test_failed_read_is_unknown_not_empty_success(self):
        self.plugin.downloader.get_torrents.return_value=(None,True)
        view=self.snapshot()
        self.assertTrue(view['live_error'])
        self.assertIsNone(view['summary']['downloading'])
        self.assertIsNone(view['summary']['upload_speed'])
        self.assertIsNone(view['sites'][0]['downloading'])
        self.assertEqual(len(view['history']),1)

    def test_capacity_and_interrupted_run_are_explicit(self):
        self.plugin._brush_config.site_download_cap=1
        self.job={'status':'running'}
        view=self.snapshot()
        self.assertEqual(view['sites'][0]['reason'],'已达到单站下载上限')
        self.assertEqual(view['job']['status'],'error')

    def test_background_run_is_single_and_never_calls_cleanup(self):
        p=PLUGIN.FlowBrush()
        p._brush_config=PLUGIN.BrushConfig({'brushsites':[1],'downloader':'test'})
        p._brush_run_lock=threading.Lock()
        p.save_data=Mock()
        p._brush_impl=Mock()
        p.check=Mock()
        with patch.object(PLUGIN.threading,'Thread') as th:
            p.run_once()
            self.assertTrue(p._brush_run_lock.locked())
            p.run_once()
            th.assert_called_once()
            self.assertEqual(th.call_args.kwargs['target'],p.brush)
            p.brush(_lock_held=True)
        self.assertFalse(p._brush_run_lock.locked())
        p._brush_impl.assert_called_once()
        p.check.assert_not_called()
        self.assertEqual(p.save_data.call_args.args[1]['status'],'success')

    def test_scheduler_honors_user_period(self):
        p=PLUGIN.FlowBrush()
        p._brush_config=PLUGIN.BrushConfig({'enabled':True,'brushsites':[1],'cron':'*/30 * * * *'})
        p._task_brush_enable=True
        with patch.object(PLUGIN.CronTrigger,'from_crontab') as cron:
            p.get_service()
            cron.assert_called_once_with('*/30 * * * *')
