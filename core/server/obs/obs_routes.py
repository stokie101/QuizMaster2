import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.server.obs.obs_handler import OBSHandler
from core.server.obs.obs_manager import OBSManager

logger = logging.getLogger(__name__)


def register_obs_routes(app: FastAPI, sio, server):
    manager = OBSManager.get_instance(server)
    _ = OBSHandler(server)

    obs_base = server.BASE_DIR / "obs"
    html_dir = obs_base / "html"
    js_dir = obs_base / "js"

    if js_dir.exists():
        app.mount("/obs/js", StaticFiles(directory=str(js_dir)), name="obs_js")

    @app.get('/obs/tab')
    async def obs_tab_page():
        path = html_dir / 'obs_tab.html'
        if not path.exists():
            raise HTTPException(404, 'OBS tab not found')
        return FileResponse(path)

    @app.get('/obs/control')
    async def obs_control_page():
        path = html_dir / 'obs_control.html'
        if not path.exists():
            raise HTTPException(404, 'OBS control not found')
        return FileResponse(path)

    @app.get('/api/obs/config')
    async def get_obs_config():
        return JSONResponse({'success': True, 'config': manager.get_config()})

    @app.put('/api/obs/config')
    async def update_obs_config(request: Request):
        data = await request.json()
        manager.update_config(data or {})
        return JSONResponse({'success': True})

    @app.post('/api/obs/connect')
    async def connect_obs():
        try:
            await manager.connect()
            return JSONResponse({'success': True, 'connected': manager.is_connected()})
        except Exception as exc:
            return JSONResponse({'success': False, 'connected': False, 'error': str(exc)}, status_code=400)

    @app.post('/api/obs/disconnect')
    async def disconnect_obs():
        await manager.disconnect()
        return JSONResponse({'success': True})

    @app.post('/api/obs/test')
    async def test_obs():
        result = await manager.test_connection()
        return JSONResponse(result)

    @app.get('/api/obs/scenes')
    async def get_obs_scenes():
        try:
            scenes = await manager.get_scenes()
            return JSONResponse({'success': True, 'scenes': scenes})
        except Exception as exc:
            return JSONResponse({'success': False, 'error': str(exc), 'scenes': []}, status_code=400)

    @app.get('/api/obs/current_scene')
    async def get_obs_current_scene():
        try:
            scene_name = await manager.get_current_scene()
            return JSONResponse({'success': True, 'sceneName': scene_name})
        except Exception as exc:
            return JSONResponse({'success': False, 'error': str(exc), 'sceneName': ''}, status_code=400)

    @app.post('/api/obs/switch_scene')
    async def switch_obs_scene(request: Request):
        try:
            data = await request.json()
            scene_name = str((data or {}).get('sceneName', '') or '').strip()
            if not scene_name:
                return JSONResponse({'success': False, 'error': 'sceneName required'}, status_code=400)
            await manager.switch_scene(scene_name)
            return JSONResponse({'success': True})
        except Exception as exc:
            return JSONResponse({'success': False, 'error': str(exc)}, status_code=400)

    @app.get('/api/obs/triggers')
    async def get_obs_triggers():
        cfg = manager.get_config()
        return JSONResponse({'success': True, 'triggers': cfg.get('sceneTriggers', [])})

    @app.put('/api/obs/triggers')
    async def update_obs_triggers(request: Request):
        data = await request.json()
        triggers = (data or {}).get('triggers', [])
        manager.update_config({'sceneTriggers': triggers})
        return JSONResponse({'success': True})

    @app.post('/api/obs/triggers/{trigger_id}/test')
    async def test_obs_trigger(trigger_id: str):
        cfg = manager.get_config()
        trigger = next((t for t in cfg.get('sceneTriggers', []) if str(t.get('id')) == str(trigger_id)), None)
        if not trigger:
            return JSONResponse({'success': False, 'error': 'Trigger not found'}, status_code=404)
        scene_name = str(trigger.get('sceneName') or '')
        if not scene_name:
            return JSONResponse({'success': False, 'error': 'Trigger missing sceneName'}, status_code=400)

        prev_scene = await manager.get_current_scene()
        await manager.switch_scene(scene_name)
        delay = int(trigger.get('returnAfterSeconds', 0) or 0)
        if delay > 0:
            await manager.schedule_return(prev_scene or '', delay)
        return JSONResponse({'success': True, 'sceneName': scene_name})

    logger.info('✅ OBS routes registered')
