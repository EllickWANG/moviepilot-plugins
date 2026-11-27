"""Read-only, credential-free data model for the brush workspace."""
import time


def number(value):
    try:
        value = float(value)
        return value if 0 <= value < float('inf') else 0
    except (ValueError, TypeError):
        return 0


def owner_of(raw, task):
    owner = task.get('site_name')
    if owner and owner != '未知':
        return owner
    tags = raw.get('tags', '') if isinstance(raw, dict) else getattr(raw, 'labels', [])
    tags = tags.split(',') if isinstance(tags, str) else tags or []
    owners = {tag.strip()[3:] for tag in tags if isinstance(tag, str) and tag.strip().startswith('站点/')}
    return next(iter(owners)) if len(owners) == 1 else '未知站点'


def live_row(raw, tasks):
    """Explicit allow-list: never serialize trackers, magnet URIs, paths or credentials."""
    if isinstance(raw, dict):
        hash_id = raw.get('hash', '')
        progress = min(number(raw.get('progress')), 1)
        state = raw.get('state', 'unknown')
        row = dict(hash=hash_id, title=raw.get('name', ''), state=state,
                   progress=progress, size=number(raw.get('total_size') or raw.get('size')),
                   uploaded=number(raw.get('uploaded')), downloaded=number(raw.get('downloaded')),
                   upload_speed=number(raw.get('upspeed')), download_speed=number(raw.get('dlspeed')),
                   seeding_hours=number(raw.get('seeding_time')) / 3600,
                   added_on=number(raw.get('added_on')),
                   seeds=number(raw.get('num_seeds')), peers=number(raw.get('num_leechs')))
    else:
        hash_id = getattr(raw, 'hashString', '')
        progress = min(number(getattr(raw, 'progress', 0)) / 100, 1)
        row = dict(hash=hash_id, title=getattr(raw, 'name', ''), state=str(getattr(raw, 'status', 'unknown')),
                   progress=progress, size=number(getattr(raw, 'total_size', 0)),
                   uploaded=number(getattr(raw, 'uploaded_ever', 0)), downloaded=number(getattr(raw, 'downloaded_ever', 0)),
                   upload_speed=number(getattr(raw, 'rate_upload', 0)), download_speed=number(getattr(raw, 'rate_download', 0)),
                   seeding_hours=None, added_on=0, seeds=None, peers=None)
    row['site'] = owner_of(raw, tasks.get(hash_id, {}))
    row['status'] = ('error' if row['state'] in ('error', 'missingFiles') else
                     'paused' if row['state'] in ('pausedDL', 'pausedUP', 'stoppedDL', 'stoppedUP', 'stopped') else
                     'seeding' if progress == 1 else 'downloading' if row['download_speed'] else 'waiting')
    return row


def build_workspace(plugin, sites, downloaders):
    config = plugin._brush_config
    tasks = plugin.get_data('torrents') or {}
    job = plugin.get_data('workspace_run') or {}
    if job.get('status') == 'running' and not plugin._brush_run_lock.locked():
        job = {**job, 'status': 'error', 'message': '上次选种已中断，可重新运行'}
    live, error = [], ''
    try:
        downloader = plugin.downloader
        if not downloader:
            raise RuntimeError('unavailable')
        raw, failed = downloader.get_torrents(tags=config.brush_tag)
        if failed or raw is None:
            raise RuntimeError('query_failed')
        live = [live_row(row, tasks) for row in raw]
    except Exception:
        error = '下载器查询失败，当前任务数和传输速度待确认'
    rows = []
    downloaded_count = sum(row['progress'] < 1 for row in live)
    for site in sites:
        name, sid = site['name'], site['id']
        cfg = config.get_site_config(name)
        current = [r for r in live if r['site'] == name]
        enabled = sid in config.brushsites
        active = sum(r['progress'] < 1 for r in current)
        reasons = []
        if not enabled:
            reasons.append('已暂停新增，已有任务继续维护')
        elif not config.enabled:
            reasons.append('自动刷流已关闭，可手动运行')
        elif error:
            reasons.append('下载器状态待确认')
        elif cfg.site_download_cap and active >= cfg.site_download_cap:
            reasons.append('已达到单站下载上限')
        elif config.maxdlcount and downloaded_count >= config.maxdlcount:
            reasons.append('总下载名额已满')
        else:
            reasons.append('等待下一轮选种')
        result = (job.get('sites') or {}).get(name, {})
        rows.append(dict(id=sid, name=name, enabled=enabled, downloading=active if not error else None,
                         seeding=sum(r['progress'] == 1 for r in current) if not error else None,
                         upload_speed=sum(r['upload_speed'] for r in current) if not error else None,
                         uploaded=sum(r['uploaded'] for r in current) if not error else None,
                         cap=cfg.site_download_cap, reason=reasons[0], last_result=result,
                         policy=dict(seed_hours=cfg.seed_time, hr_hours=cfg.hr_seed_time,
                                     hr=cfg.site_hr_active, size=cfg.size, min_peers=cfg.min_peers,
                                     seeder=cfg.seeder, pubtime=cfg.pubtime)))
    history = sorted(tasks.values(), key=lambda t: number(t.get('time')), reverse=True)[:200]
    history = [dict(title=t.get('title', ''), site=t.get('site_name', '未知站点'),
                    added_on=number(t.get('time')), deleted=bool(t.get('deleted')),
                    size=number(t.get('size')), uploaded=number(t.get('uploaded')),
                    downloaded=number(t.get('downloaded'))) for t in history]
    return dict(version=plugin.plugin_version, enabled=config.enabled, downloader=config.downloader,
                checked_at=int(time.time()), live_error=error, job=job, sites=rows, tasks=live,
                history=history, history_total=len(tasks), downloaders=downloaders,
                summary=dict(download_speed=sum(t['download_speed'] for t in live) if not error else None,
                             upload_speed=sum(t['upload_speed'] for t in live) if not error else None,
                             downloading=downloaded_count if not error else None,
                             seeding=sum(t['progress'] == 1 for t in live) if not error else None,
                             uploaded=sum(t['uploaded'] for t in live) if not error else None,
                             downloaded=sum(t['downloaded'] for t in live) if not error else None,
                             size=sum(t['size'] for t in live) if not error else None),
                policy=dict(max_downloads=config.maxdlcount, site_cap=config.site_download_cap,
                            cycle_cap=config.site_cycle_cap, size=config.size, seeder=config.seeder,
                            min_peers=config.min_peers, pubtime=config.pubtime, disk_gb=config.disksize,
                            max_upload_kb=config.maxupspeed, cron=config.cron,
                            freeleech=config.freeleech, demand_priority=config.demand_priority))
