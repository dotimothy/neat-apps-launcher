# Neat Apps Launcher

<p align="center"><img src="docs/media/run.gif" alt="Pressing Run on Single-Stream Object Detector: Insight starts a sample stream, the app runs YOLO26 on the MLA, and its detections play live in the Insight viewer inside the launcher" width="860"></p>
<p align="center"><em>Every Neat example app on SiMa.ai Modalix, out of the box: one page to install them, get their models, run them on the MLA and watch the results live in Neat Insight.</em></p>

## Metadata

| Field | Value |
|---|---|
| Category | Launcher · Examples · Device manager |
| Difficulty | Beginner |
| Tags | SiMa.ai, Modalix, Neat, MLA, Insight, Out of the box |
| Languages | Python, JavaScript |
| Status | Preview |
| Binary Name | `neat-apps` |
| Apps | The examples in [sima-neat/apps](https://github.com/sima-neat/apps) (`main`) |
| Sibling | [Neat Studio Launcher](https://github.com/dotimothy/neat-studio-launcher) for the full web studios |

## Preview

<table>
<tr>
<td width="50%"><img src="docs/media/intro.gif" alt="Launch animation: a Modalix chip warps in through a starfield, then the app categories and the home page with the board setup checklist"><br><b>Launch Animation.</b> A SiMa.ai Modalix chip warps in, the app categories appear, and the home page shows the board setup checklist: sima-cli, the apps bundle, Neat Core, Insight, pyneat, ffmpeg and the sample videos.</td>
<td width="50%"><img src="docs/media/catalog.gif" alt="Browsing the catalog: the live-stream filter, a search for segment, then an app page with its README"><br><b>The Catalog.</b> Every example app with its preview, model and inputs. Filter by category, input or language, search, then open an app's page with its README, models and settings.</td>
</tr>
<tr>
<td><img src="docs/media/results.gif" alt="Running YOLO26 Object Detector on the sample images, its log, then the annotated results and a full-screen viewer"><br><b>Results.</b> YOLO26 Object Detector runs on the bundled photos; its log streams live, then the annotated images open in a gallery with a full-screen viewer.</td>
<td><img src="docs/media/manager.gif" alt="The Manager: board stats, running apps and Insight, bundle and launcher updates, the setup log and a shell on the board"><br><b>Manager.</b> Board and MLA memory stats, running apps and Insight with Stop and Kill, the apps bundle and launcher update checks, every log, and a shell on the board.</td>
</tr>
</table>

## Concept

The examples in `sima-neat/apps` each need several manual steps before they run: install the apps bundle, download the model from the Developer Portal, replace the placeholders in `config.yaml` (`<model-path>`, `<rtsp-url-1>`, `<insight-host-ip>`), start Insight, set up an RTSP source in Insight, then start the binary. The launcher does all of it.

- **Board Setup** checks what the apps need, and fixes what it can from the page:

  | Prerequisite | Checked | When it's missing |
  |---|---|---|
  | Firmware | the platform build in `/etc/buildinfo`, against the model zoo's version (3.0 for model zoo 3.0.0; `platform_version` in `apps.yaml` overrides it) | **Update Firmware…** explains, then types `sima-cli update` into the Shell for you to start: it flashes and reboots the board. Set Up Everything stops until the firmware is new enough. |
  | sima-cli | `sima_cli` in `apps.yaml`, else `PATH` | **Install** runs SiMa.ai's installer (into `/data/sima-cli`) |
  | Developer Portal | sima-cli's sign-in (`~/.sima-cli`), needed to download models | **Sign In** runs `sima-cli login` in the Shell: open the link and enter the code |
  | Neat Core | the `sima-neat` package, against the Core the bundle was built for (`deps/neat-core.json`) | **Install Neat Core** (`sima-cli neat install -t minimal core`, also the pyneat environment); Set Up Everything does it when needed |
  | pyneat | `~/pyneat`, for the Python versions | **Link It** when the environment is already in `/media/nvme/pyneat`; otherwise Neat Core brings it |
  | Neat Studio Launcher (optional) | installed in `/media/nvme/neat-studio-launcher`, and answering on port 8090 (`studio_launcher_port`) | **Install** runs its installer and starts it; **Start** starts it. Neat GenAI Studio's card does either when needed, then offers to open the studio. |
- **Set Up Everything** (one button) installs what is missing:
  - the prebuilt apps bundle with `sima-cli neat install apps`, which also brings the matching Neat Core and Neat Insight
  - Neat Core, when it is missing or not the version the bundle was built for
  - `ffmpeg`, which Insight uses to stream videos (from the board's apt, with passwordless sudo)
  - 16 sample videos (720p, H.264) for the streaming apps
- **A catalog** of every app: search, plus category, input (image folder, live stream, multi-stream, video, camera) and language filters. Cards show the app's preview, model and state.
- **Run** on any app:
  1. stops the other app on the MLA, or runs alongside it if you choose
  2. downloads the app's model into `prebuilt-apps/models/` with `sima-cli` (once)
  3. for a streaming app, starts Insight and its sample streams (`rtsp://<board>:8554/srcN`) and picks free Insight channels, so two apps never draw over each other
  4. writes `runs/<app>/config.yaml` with everything filled in (the app's own config is never changed), checks it with `--validate-config-only` where the app supports it, and starts the C++ binary or the Python version (in `~/pyneat`)
- **An app page** per app:
  - **Live**: Insight's viewer for the app's channels, inside the page
  - **Results**: annotated images, or the benchmark report
  - **Log**: the app's output, live
  - **About**: the app's README
  - **Config**: the generated config; edit and save it to use your own
  - on the side: C++ or Python, which model (all of the app's variants, with download state), and sample videos or your own RTSP URLs
- **Insight** page: Insight inside the launcher, with start, stop and restart.
- **Manager**:
  - running apps with PID, CPU, memory and uptime, and Stop and Kill
  - the apps bundle version against the latest published one (one-click update), and launcher updates
  - Reset MLA
  - the setup, Insight and app logs, and a shell on the board

Apps that do not run on the board itself are left out (`pcie-high-density-multi-stream-object-detector`, and `detection-to-vlm-assistant` until its VLM is published). Neat GenAI Studio links to Neat Studio Launcher.

> **Security:** the launcher has no login. Anyone who can reach it can run apps and use the shell, so use it on a trusted network only.

## Install

On the Modalix DevKit:

```bash
cd /media/nvme
curl -fsSL https://raw.githubusercontent.com/dotimothy/neat-apps-launcher/main/install.sh | bash
```

`setup.sh` creates `.venv` (Flask, Flask-SocketIO, psutil, PyYAML), a self-signed certificate, the **`neat-apps`** alias and a **Neat Apps Launcher** desktop icon.

Model downloads come from the SiMa Developer Portal, so `sima-cli` must be logged in once: run `sima-cli login` in Manager → Shell, or in any shell on the board.

## Run

```bash
neat-apps                # or ./run.sh   (Ctrl+C stops it)
neat-apps --daemon       # in the background
neat-apps --open-browser
```

Then open `https://<board-ip>:8091/` and accept the self-signed certificate once. Insight and its viewer use their own certificates; the first time one opens inside the launcher, a **Trust** button walks you through accepting it.

| Command | What it does |
|---|---|
| `neat-apps stop` | Stop the launcher. Apps and Insight keep running. |
| `neat-apps status` | Show whether it is running, and where |
| `neat-apps update` | Update the launcher from GitHub |
| `neat-apps logs` | Follow the background launcher's log |
| `--port N`, `--host H`, `--no-https` | Change where it listens |

## Configure

`src/launcher/apps.yaml` holds the settings:
- install root and bundle branch
- sima-cli and pyneat paths
- Insight port and sample videos

It also holds per-app overrides:
- hidden apps and notes
- bundled clips to stream
- default models and stream count
- extra config keys

To override any of it without editing that file, put the same keys in `config.local.yaml` next to `run.sh`:

```yaml
install_root: /media/nvme
insight:
  port: 9900
apps:
  - id: multi-stream-object-detector
    streams: 8
    config: { inference.min_score: 0.4 }
```

Environment variables:
- where it listens: `LAUNCHER_PORT` (8091), `LAUNCHER_HOST`, `LAUNCHER_HTTPS=0`
- paths: `NEAT_APPS_ROOT`, `NEAT_APPS_BUNDLE`, `NEAT_APPS_PYNEAT`, `SIMA_CLI_BIN`, `NEAT_LAUNCHER_CACHE`
- `NEAT_LAUNCHER_STOP_GRACE` (60 s)

### The catalog

`src/launcher/catalog.json` is generated from a `sima-neat/apps` checkout, because the bundle on the board has no `tests/` or `portal/` folders:

```bash
python3 tools/build_catalog.py /path/to/sima-neat/apps --previews
```

For each app it reads:
- its README, the same way the developer portal does (`scripts/generate_catalog.py`)
- its models, from `tests/test-scope.yaml`
- its input and Insight output, from `src/common/config.yaml`

## Verify

```bash
curl -k https://localhost:8091/health
curl -k https://localhost:8091/api/platform
curl -k -X POST https://localhost:8091/api/apps/yolo26-object-detector/run -H 'Content-Type: application/json' -d '{"lang":"python"}'
curl -k https://localhost:8091/api/apps/yolo26-object-detector/outputs
```

## Source Files

- Web app and API: `src/launcher/app.py`, entry point `src/launcher/main.py`
- Runs, states, Insight streams and channels: `src/launcher/service.py`
- Processes, jobs and logs: `src/launcher/procman.py`
- Catalog and settings: `src/launcher/registry.py`, `catalog.json`, `apps.yaml`
- Filling in configs: `src/launcher/runconfig.py`; models: `src/launcher/models.py`
- Neat Insight API client: `src/launcher/insight.py`; bundle, Core and ffmpeg: `src/launcher/bundle.py`
- Launcher updates: `src/launcher/updates.py`; shell: `terminal.py`; board stats: `sysinfo.py`
- UI: `src/launcher/templates/index.html`, `static/launcher.js`, `static/launcher.css` and `static/apps.css`
- Scripts: `install.sh`, `setup.sh`, `run.sh`, `src/common/desktop-launch.sh`

## Development From Source

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest pillow
.venv/bin/python -m pytest tests/python -q

# The full UI on a PC, with a fake board (fake bundle, sima-cli and Insight):
python3 tests/python/fixtures/fakeboard.py /tmp/fakeboard --apps-repo /path/to/sima-neat/apps
NEAT_LAUNCHER_CONFIG=/tmp/fakeboard/config.local.yaml ./run.sh --no-https
```

The README GIFs are recorded from a running launcher with Playwright (it drives the installed Google Chrome) and ffmpeg. The `run` and `results` scenes start apps, so the script skips them while any other app is running on the board:

```bash
pip install playwright
python3 tools/record_gifs.py https://<board-ip>:8091            # all scenes into docs/media/
python3 tools/record_gifs.py https://<board-ip>:8091 --only run
```
