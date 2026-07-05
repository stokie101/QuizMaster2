# QuizMaster widget hosting (Cloudflare Tunnel)

QuizMaster generates official OBS browser-source URLs like:

```
https://widgets.quizmaster.liveforge.online/u/<public_widget_id>/leaderboard?session=<id>
https://widgets.quizmaster.liveforge.online/u/<public_widget_id>/quiz_display?session=<id>
https://widgets.quizmaster.liveforge.online/u/<public_widget_id>/timer_display?session=<id>
https://widgets.quizmaster.liveforge.online/u/<public_widget_id>/quiz_controls?session=<id>
```

These are served **by the running desktop app itself** (the FastAPI bridge
server on `http://127.0.0.1:5555`). The public host reaches that local server
through a Cloudflare Tunnel — the exact model the LiveForge app uses for
`widgets.liveforge.online`. There is no separate widget backend, static site,
Worker, or database to deploy.

## Why a tunnel is required

- The bridge server binds to `127.0.0.1:5555` only; it is not reachable from
  the public internet on its own.
- `/u/<public_widget_id>/...` routes, `?session=` validation, owner/control
  tokens, and the Socket.IO live-state rooms already exist in the app
  (`core/server/public_widget_routes.py`, `core/server/widget_sessions.py`).
- The only missing piece is a public hostname that forwards to `127.0.0.1:5555`.

## One-time Cloudflare setup

The host lives in the existing `liveforge.online` zone (already in Cloudflare),
so no new domain registration is needed.

1. **Create a named tunnel** on the machine that runs QuizMaster:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create quizmaster-widgets
   ```
2. **Route the public hostname to the tunnel** (adds the DNS CNAME
   `widgets.quizmaster.liveforge.online -> <tunnel-id>.cfargotunnel.com`):
   ```bash
   cloudflared tunnel route dns quizmaster-widgets widgets.quizmaster.liveforge.online
   ```
3. **Point the tunnel ingress at the local bridge server** (`~/.cloudflared/config.yml`):
   ```yaml
   tunnel: quizmaster-widgets
   credentials-file: /path/to/<tunnel-id>.json
   ingress:
     - hostname: widgets.quizmaster.liveforge.online
       service: http://127.0.0.1:5555
     - service: http_status:404
   ```
4. **Run the connector** alongside the desktop app:
   ```bash
   cloudflared tunnel run quizmaster-widgets
   ```

## Verifying

With the app running and the tunnel up:

```bash
# No session -> the desktop server's own 400 (proves the tunnel reaches it):
curl -i https://widgets.quizmaster.liveforge.online/u/<public_widget_id>/leaderboard
# -> {"detail":"A widget session ID is required"}
```

Then load a full app-generated URL (with `?session=`) as an OBS browser source.

## Notes / limits

- A single named tunnel forwards to **one** machine. The `public_widget_id` in
  the path scopes to whichever signed-in user is running that instance — the
  same single-origin model LiveForge uses today. Serving many users' machines
  under one shared host would require a central relay (Worker + Durable Object),
  which neither app currently implements.
- The host is overridable without a rebuild via the `HOSTED_WIDGETS_BASE_URL`
  (or `WIDGETS_BASE_URL`) environment variable; the packaged default lives in
  `config/production.env` and `core/server/url_config.py`.
