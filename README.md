# PMO Resource Management Agent

A chat-based assistant for managing resources, projects and allocations. It ships in two forms that share the same agent logic:

| Version | Entry point | Where data is stored | Use it for |
|---|---|---|---|
| **Web app (Vercel)** | `public/index.html` + `api/index.py` (Flask) | Your browser's `localStorage` | Deploying online |
| **Streamlit app** | `streamlit_app.py` | Local SQLite file `data/pmo.db` | Running on your own machine |

> Vercel cannot host Streamlit (it needs a long-running server) and its filesystem is not persistent. So the Vercel version keeps your data in the browser and sends it to a stateless API with each message.

## Prerequisites

- Python 3.10 or newer
- Git
- Node.js (only for the Vercel CLI)

```powershell
python --version
git --version
node --version
```

## Get the code

```powershell
git clone https://github.com/kashimahtta/pmo-agent.git
cd pmo-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activate script, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Option A: Run the web app locally (same code as Vercel)

```powershell
pip install -r requirements.txt
npm install -g vercel
vercel dev
```

`vercel dev` asks you to log in and link the project the first time, then serves the site at <http://localhost:3000>.

## Option B: Run the Streamlit app locally

```powershell
pip install -r requirements-streamlit.txt
python -m streamlit run streamlit_app.py
```

Open <http://localhost:8501>. Data is stored in `data/pmo.db`.

Other Streamlit commands:

```powershell
python -m streamlit run streamlit_app.py --server.port 8600
python -m streamlit run streamlit_app.py --server.address 0.0.0.0
python -m streamlit run streamlit_app.py --server.headless true
Remove-Item data -Recurse -Force
```

## Deploy to Vercel

### From the dashboard (easiest)

1. Push this repo to GitHub (already done if you cloned it).
2. Go to <https://vercel.com/new> and import `kashimahtta/pmo-agent`.
3. Leave **Framework Preset** as **Other**. Leave the build and output settings empty.
4. Click **Deploy**.

Every `git push` to `main` redeploys automatically.

### From the command line

```powershell
npm install -g vercel
vercel login
vercel          # preview deployment
vercel --prod   # production deployment
```

### How it is wired

- `public/` is served as static files (`index.html` is the UI).
- `api/index.py` is a Flask app that Vercel runs as a Python serverless function. `vercel.json` routes `/api/*` to it and bundles `agent.py` and `db.py`.
- `requirements.txt` contains only Flask, which is all Vercel needs. Streamlit's dependencies are in `requirements-streamlit.txt`.
- The browser sends its data with each chat message. The function loads it into an in-memory SQLite database, runs the agent and returns the updated data, which the browser saves to `localStorage`. Nothing is kept on the server.

Because data lives in the browser, it is per device and per browser. Use **Export** in the app to download a JSON backup. Clearing site data erases it.

## Using the app

1. Enter your name on the sign-in screen.
2. In the **Agent** tab, type `help`, or `load sample data` to try demo data.
3. Use the other tabs to see the dashboard, resources, projects and allocations.

| Message | What it does |
|---|---|
| `add resource Alice role Developer skills python, sql capacity 40 rate 80` | Adds a person |
| `add project Apollo budget 50000` | Adds a project |
| `assign Alice to Apollo 20 hours` | Allocates hours per week |
| `who is free?` / `who is overallocated?` | Capacity checks |
| `who knows python for 15 hours?` | Finds suitable people |
| `summary` / `utilization` / `cost of Apollo` | Reports |
| `rebalance` | Suggests how to fix overallocation |
| `delete project Apollo` | Deletes after confirmation |

## Project structure

| File | Purpose |
|---|---|
| `public/index.html` | Web UI for Vercel |
| `api/index.py` | Flask API (Vercel serverless function) |
| `agent.py` | Chat agent: understands messages and acts on the data |
| `db.py` | SQLite helpers (file mode for Streamlit, in-memory mode for the API) |
| `streamlit_app.py` | Streamlit UI for local use |
| `vercel.json` | Vercel routing and bundling |
| `requirements.txt` | Vercel dependencies (Flask) |
| `requirements-streamlit.txt` | Streamlit dependencies |

## Troubleshooting

- **`PYTHON_ENTRYPOINT_NOT_FOUND ... app.py`:** an old deployment of the Streamlit version. Redeploy the latest `main`; the Streamlit file is now `streamlit_app.py`.
- **`python` not found:** install Python from <https://www.python.org/downloads/> and tick "Add Python to PATH".
- **`streamlit` not found:** activate the virtual environment and run `pip install -r requirements-streamlit.txt`.
- **Data disappeared on the web app:** you are on a different browser or device, or site data was cleared.
