# Dashboard / Web Console Audit

Date: 2026-03-29
Author: yu

## Scope

Audited:

- `web-console/index.html`
- `web-console/console.html`
- `web-console/js/app.js`
- `web-console/css/style.css`
- `web-console/api/service.py`
- `web-console/nginx/openra.ananthe.party`
- `web-console/DESIGN.md`
- `web-console/ROADMAP.md`
- `main.py` for the actual dashboard bridge/data source

## Executive summary

The current web console is not just a dashboard. It is an overloaded operations cockpit that mixes:

- service control
- live VNC
- copilot chat
- enemy-agent chat/control
- generic logs
- runtime status
- strategy debug
- jobs debug

That is why the user feels it is "很乱". The problem is not one bad widget. The problem is information architecture: too many unrelated responsibilities are competing on one screen, and several parts have drifted out of sync with the backend.

The most important concrete issues are:

1. The frontend/nginx expect the console bridge on `8090`, but `main.py` starts `DashboardBridge` on `8092`.
2. The page mixes operator controls, conversational UI, debug tooling, and higher-level strategy state in one layout without a primary view.
3. There is no first-class task model anywhere in the UI, so a kanban/task-system view cannot be added cleanly as a small patch.
4. There is obvious code/document drift: `index.html` is much more advanced than `ROADMAP.md`, and `console.html` is still present as a second, older UI.

## 1. What the current dashboard shows

### Top service bar

`web-console/index.html:11-58`

The top bar shows:

- source/build controls: `Pull`, `编译`
- game lifecycle controls: `游戏`, `停止`
- AI lifecycle controls: `AI`, `停AI`
- reset/new-match control: `新对局`
- enemy-agent lifecycle controls: `启动敌方`, `关闭敌方`
- three status dots: game, AI, enemy agent

This is operator tooling, not dashboard information.

### Main body

`web-console/index.html:61-113`

The middle area is split into:

- left: noVNC iframe for the actual game screen
- right: chat panel with two tabs
  - `副官`: send natural-language commands to the human-side AI
  - `敌方`: send messages to the enemy agent

The copilot tab also includes quick commands such as `查询状态`, `展开`, `电厂`, `建造3步兵`, `探索地图`.

### Bottom debug area

`web-console/index.html:116-260`

The bottom debug panel contains six tabs:

- `Logs`
- `Status`
- `Terminal` placeholder
- `Enemy`
- `Strategy`
- `Jobs`

This means the page already contains three different conceptual products at once:

- game operator console
- chat console
- engineering/debug console

### Strategy tab

`web-console/index.html:206-251`

The strategy tab is the most "dashboard-like" area. It shows:

- auto-bridge status
- enable/disable controls
- strategy command input
- company roster
- debug log
- a fog-of-war map canvas
- company centers/targets/member markers

### Jobs tab

`web-console/index.html:253-280`

The jobs tab shows:

- runtime summary line
- human jobs list
- enemy jobs list

This is the closest existing surface to a task system, but it is still a debug dump of current runtime jobs, not a task board.

## 2. Tech stack

### Frontend

`web-console/index.html`
`web-console/js/app.js`
`web-console/css/style.css`

The frontend stack is:

- plain HTML
- plain CSS
- vanilla JavaScript
- no frontend framework
- no component system
- no client-side router
- no formal state store

`web-console/js/app.js` is a large single script that owns:

- connection setup
- websocket message routing
- service API calls
- chat rendering
- enemy debug rendering
- strategy-map rendering
- jobs rendering
- debug-panel resize logic

This is functionally workable, but it is already beyond the comfortable size for a single-file vanilla JS UI.

### Backend / transport

There are two distinct backend paths:

1. Console bridge from `main.py`
   - `DashboardBridge` started in `main.py:1190-1193`
   - strategy updates from `main.py:585-599`
   - jobs updates from `main.py:631-642`
   - human command flow from `main.py:858-980`

2. Service control API
   - FastAPI app in `web-console/api/service.py`
   - REST endpoints in `web-console/api/service.py:131-234`
   - separate WebSocket endpoint at `/ws` in `web-console/api/service.py:85-105`

### Infra

`web-console/nginx/openra.ananthe.party`

Infra stack is:

- nginx reverse proxy
- noVNC iframe for display/control
- FastAPI for service controls
- WebSocket for console bridge

No React, no Vue, no TypeScript, no xterm integration yet.

## 3. How it receives data

### A. Console bridge WebSocket

Frontend:

- `web-console/js/app.js:18-22`
- `web-console/js/app.js:94-130`

Backend:

- `main.py:52`
- `main.py:1190-1193`

The main live data path is a WebSocket opened by the frontend to the console bridge. Over that socket the UI receives:

- command status / result
- enemy chat / enemy status / enemy tick detail
- strategy state / strategy log / strategy trace
- jobs state
- shared chat history
- reset notifications

The same socket is also used for sending:

- copilot commands
- enemy chat
- enemy-control actions
- strategy-control actions

### B. Service REST API

Frontend:

- `web-console/js/app.js:507-560`

Backend:

- `web-console/api/service.py:131-234`

The top-bar service buttons use `fetch()` against the FastAPI service API for:

- pull
- build
- start/stop game
- start/stop AI
- status polling

### C. VNC iframe

Frontend:

- `web-console/js/app.js:12-16`
- `web-console/js/app.js:79-90`

Infra:

- `web-console/nginx/openra.ananthe.party:18-29`

The game screen is an embedded noVNC page inside an iframe.

## 4. What is messy / broken

### 4.1 Port drift: frontend/nginx say `8090`, backend says `8092`

Evidence:

- frontend WebSocket URL uses `8090`: `web-console/js/app.js:18-22`
- nginx proxies `/api/` to `8090`: `web-console/nginx/openra.ananthe.party:31-42`
- `main.py` starts dashboard bridge on `8092`: `main.py:51-52`, `main.py:1190-1193`, `main.py:1246`

This is a hard integration bug, not just code mess.

### 4.2 Too many product concepts on one page

The UI currently combines:

- remote desktop
- service control
- AI chat
- enemy chat
- jobs inspector
- strategy visualizer
- debug log console

This produces a "kitchen sink" page instead of a clear dashboard. There is no single primary question the page answers.

### 4.3 No first-class task model

The page has:

- chats
- logs
- jobs
- company state

But it does not have:

- tasks
- owners
- phases
- dependencies
- blocked reasons
- resource reservations
- progress
- completion history

So a task-system kanban cannot be added cleanly by restyling the current jobs panel. The backend payload model itself is missing the concepts.

### 4.4 `console.html` is a second, older UI

`web-console/console.html`

This file is an older inline-style version of the console. Keeping both `index.html` and `console.html` is confusing:

- two entry pages
- two layouts
- duplicated intent
- unclear source of truth

It is a classic sign of UI drift.

### 4.5 Design/roadmap documents are stale

`web-console/ROADMAP.md` still marks major chat/debug features as not done, while `index.html` and `app.js` already contain enemy debug, strategy debug, and jobs debug.

This means architecture decisions are no longer documented from the actual codebase state.

### 4.6 Service API log streaming exists but is effectively unused

`web-console/api/service.py:85-105`

The FastAPI service app exposes a `/ws` endpoint for real-time log broadcasting, but `index.html` does not connect to it. The page only uses REST calls for service actions.

Result:

- service action feedback is mostly request/response level
- the dedicated service WebSocket path is dead weight from the current UI's perspective

### 4.7 URL config is inconsistent

`web-console/js/app.js:24-28` defines `serviceApiUrl`, but the actual service calls build URLs manually in `serviceAction()` and `refreshStatus()` at `web-console/js/app.js:507-560`.

That means:

- configuration is duplicated
- one config path is unused
- future path changes are easy to break

### 4.8 Layout pressure is real

`web-console/css/style.css`

Notable layout traits:

- chat panel capped at `max-width: 400px`
- main content consumes nearly full viewport height
- debug panel default content height is `1000px`
- many secondary tools live below the fold

This layout is acceptable for an engineering console, but not for a dashboard that should foreground tasks and progress.

### 4.9 Terminal tab is placeholder-only

`web-console/index.html:179-184`

The terminal tab advertises capability that does not exist. In a cluttered page, dead tabs make the clutter feel worse.

## 5. What would need to change to support a kanban / task-system view

### Backend changes first

This cannot be solved at the CSS layer.

The backend needs explicit task objects, not just logs and job snapshots. At minimum, the dashboard bridge should broadcast something like:

```json
{
  "task_id": "task_123",
  "title": "Encircle enemy armor at east ridge",
  "layer": "tactics",
  "owner": "combat_expert",
  "status": "running",
  "phase": "staging",
  "priority": "high",
  "resources": ["actor:123", "actor:456"],
  "depends_on": ["task_101"],
  "blocked_reason": "",
  "progress": 0.45,
  "summary": "Flank group moving into position",
  "updated_ms": 1774760000000
}
```

I would add two event families:

- `task_snapshot`
- `task_event`

And make them the primary board feed.

### UI information architecture changes

The current page should be split conceptually into:

1. Operations
   - VNC
   - service controls
   - coarse runtime health

2. Tasks
   - kanban lanes
   - card details
   - filters by owner/layer/status

3. Diagnostics
   - logs
   - raw jobs
   - strategy traces
   - terminal

Right now all three are blended into one surface.

### Minimal viable kanban layout

The cleanest direction is:

- top: compact operator bar
- center-left: task board as the primary surface
- center-right: selected-task detail panel
- optional bottom or separate route: diagnostics
- VNC either as a resizable side panel or a separate "Ops" tab

If VNC remains the dominant central area, the kanban board will always feel secondary and cramped.

### Data model changes

The board needs cards derived from expert/runtime state, not directly from chat messages.

Recommended columns:

- `Inbox` or `Queued`
- `Ready`
- `Running`
- `Blocked`
- `Done`

Recommended card fields:

- task title
- owner expert
- layer (`strategy`, `tactics`, `micro`, `economy`, `intel`)
- bound resources
- current phase
- short rationale
- last update time

### Code structure changes

The frontend should stop treating `app.js` as one giant mutable script. Even without adopting React, it should at least be split into modules:

- `connections.js`
- `service_api.js`
- `chat_view.js`
- `task_board.js`
- `strategy_view.js`
- `jobs_view.js`
- `layout.js`

If the team is open to a proper rewrite, a small component-based frontend would be justified here because the page is no longer a static control panel.

### Migration recommendation

My recommendation is:

1. Fix transport drift first.
   - unify the actual console-bridge port/path
2. Decide the page's primary job.
   - if it is now a task dashboard, make tasks central and move debug tools out of the primary view
3. Introduce explicit task/task-event payloads in the backend
4. Treat `Jobs` and `Strategy` as supporting diagnostics, not the main board
5. Remove or archive `console.html`
6. Update `DESIGN.md` / `ROADMAP.md` so the docs match reality

## Bottom line

The current console is useful as an engineering control room, but it is not organized like a task dashboard. To support a kanban/task-system view well, the project needs:

- a first-class task model in the backend
- a cleaner separation between operations, tasks, and diagnostics
- a frontend rewrite or at least modular refactor
- cleanup of obvious drift (`8090` vs `8092`, duplicate UI entrypoints, stale docs)
