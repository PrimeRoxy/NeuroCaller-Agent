# NeuroCaller

AI sales-calling agent built on Google ADK, with a web platform to configure the agent, chat with it, and watch every call.

## Run

```bash
uvicorn main:app --reload --port 8000
```

- `http://localhost:8000/` is the landing page.
- `http://localhost:8000/app` is the platform. It asks for an organisation ID and user ID, or you can link straight in with `/app?org_id=…&user_id=…`.
- `http://localhost:8000/docs` lists the API.

## Frontend

The frontend is plain HTML, CSS, and JavaScript modules in `utils/` (`index.html`, `app.html`, and `assets/`), served by FastAPI. There is no build step. Only `utils/assets` is exposed as static files, so the Python modules in `utils/` stay private.

| Screen | Data source |
| --- | --- |
| Command center | `/api/dashboard/stats/summary`, `/calls/by-user-org`, `/api/dashboard/campaigns`, `/ws/live-calls` |
| Live monitor | `/ws/live-calls`, `/quick-call`, `/voices` |
| Campaigns | `/api/dashboard/campaign/{id}` and its `/progress`, `/calls`, `/events`, `/pause`, `/resume`, `/cancel`; `/api/scheduler/tasks` |
| Agent studio | `/api/allyroid/config`, `/voices`, `/rented-numbers`, `/api/salesforce/check` |
| Copilot | `/salesmate` |
| Calls, Leads, Analytics | `/calls/by-user-org`, `/api/dashboard/stats/summary` |
| Settings | `/health` |
