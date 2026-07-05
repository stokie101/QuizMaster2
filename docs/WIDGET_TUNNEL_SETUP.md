# QuizMaster widget hosting

QuizMaster generates official OBS browser-source URLs like:

```
https://widgets.liveforge.online/u/<public_widget_id>/leaderboard?session=<id>
https://widgets.liveforge.online/u/<public_widget_id>/quiz_display?session=<id>
https://widgets.liveforge.online/u/<public_widget_id>/timer_display?session=<id>
https://widgets.liveforge.online/u/<public_widget_id>/quiz_controls?session=<id>
```

For QuizMaster accounts the `<public_widget_id>` is the `qmw_...` id returned by
`https://liveforge.online/api/account/me`.

## How it works

- `widgets.liveforge.online` is the shared LiveForge widget host. It is already
  live: a Cloudflare Tunnel forwards it to the running desktop app's FastAPI
  bridge server on `http://127.0.0.1:5555`. (Hitting it with no `?session=`
  returns the desktop's own `{"detail":"A widget session ID is required"}` --
  proof the host reaches a real server.)
- The desktop serves the `/u/<public_widget_id>/...` routes itself
  (`core/server/public_widget_routes.py`) and pushes live state over Socket.IO
  rooms (`core/server/widget_sessions.py`).
- The path id is validated against the **signed-in user**, not against a host or
  a prefix (`core/server/session_identity.py:validate_profile_or_warn`). So a
  `qmw_` id resolves on this host whenever the QuizMaster desktop app is the one
  running behind the tunnel -- no separate subdomain or second tunnel is needed.

## Requirements for widgets to display

1. The QuizMaster desktop app is running and signed in (so it has a `qmw_`
   `public_widget_id` from `/api/account/me`).
2. The `widgets.liveforge.online` Cloudflare Tunnel forwards to that machine's
   `http://127.0.0.1:5555`.
3. The URL loaded in OBS is a full app-generated URL that includes `?session=`
   (the app adds this automatically via `build_quiz_urls`). A bare
   `/u/<id>/quiz_display` with no session returns the 400 above by design.

## Verifying

With the app running behind the tunnel:

```bash
# No session -> the desktop server's own 400 (proves the tunnel reaches it):
curl -i https://widgets.liveforge.online/u/<qmw_id>/quiz_display
# -> {"detail":"A widget session ID is required"}
```

Then load a full app-generated URL (with `?session=`) as an OBS browser source.

## Overriding the host

The host is overridable without a rebuild via the `HOSTED_WIDGETS_BASE_URL`
(or `WIDGETS_BASE_URL`) environment variable. The packaged default lives in
`config/production.env` and `core/server/url_config.py`.

## Note / limit

A single named tunnel forwards to **one** machine, so the `public_widget_id` in
the path scopes to whichever signed-in user is running that instance. Serving
many users' machines under one shared host would require a central relay
(Worker + Durable Object), which is not implemented today.
