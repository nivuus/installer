#!/usr/bin/env python3
"""Nivuus installer web portal (FastAPI).

Served on the setup hotspot (10.42.0.1:80) or, in the Ethernet fallback, on
0.0.0.0:80. Presents the install wizard, exposes detected hardware, launches the
install engine, and streams progress over a WebSocket. Captive-portal detection
endpoints are answered so phones/laptops auto-open the wizard on connect.
"""
from __future__ import annotations

import asyncio
import os
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Make `common` importable (installer/ root).
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALLER_ROOT = os.path.dirname(WEBAPP_DIR)
if INSTALLER_ROOT not in sys.path:
    sys.path.insert(0, INSTALLER_ROOT)

from common import hardware  # noqa: E402
from models import InstallConfig  # noqa: E402
from installer_runner import InstallRunner, events_since  # noqa: E402
from packages.capabilities import detect_capabilities  # noqa: E402
from packages.conflicts import check_conflicts  # noqa: E402
from packages.discovery import discover, partition  # noqa: E402
from packages.wizard import WizardError, load_questions  # noqa: E402

app = FastAPI(title="Nivuus Installer", docs_url=None, redoc_url=None)
runner = InstallRunner()

STATIC_DIR = os.path.join(WEBAPP_DIR, "static")
TEMPLATES_DIR = os.path.join(WEBAPP_DIR, "templates")


def _page(name: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, name)) as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# Pages                                                                       #
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _page("wizard.html")


# --------------------------------------------------------------------------- #
# API                                                                         #
# --------------------------------------------------------------------------- #
@app.get("/api/hardware")
async def api_hardware() -> JSONResponse:
    # Detection touches sysfs/CLI tools; run it off the event loop.
    data = await asyncio.to_thread(hardware.detect_all)
    return JSONResponse(data)


@app.get("/api/packages")
async def api_packages(target_disk: str = "", features: str = "") -> JSONResponse:
    """Packages available on this medium, with the reason for each exclusion.

    The reason matters more than the list. A package that is simply absent
    from the wizard is indistinguishable from one that was never installed,
    and this machine has no screen to explain the difference on.
    """
    hw = await asyncio.to_thread(hardware.detect_all)
    manifests, errors = await asyncio.to_thread(discover)
    capabilities = detect_capabilities(hw, target_disk)
    selected_features = {f for f in features.split(",") if f}
    eligible, rejected = partition(manifests, capabilities, selected_features)

    def describe(manifest) -> dict:
        payload = {
            "name": manifest.name, "label": manifest.label,
            "version": manifest.version, "tier": manifest.tier,
            "claims": [r for r, _ in manifest.claims],
            # Les packages à cocher AVANT celui-ci. Le portail les affiche ;
            # il ne les coche pas à la place de l'opérateur.
            "requires_packages": list(manifest.packages),
            "questions": [],
        }
        if manifest.questions_file:
            try:
                payload["questions"] = [
                    q.to_dict() for q in load_questions(
                        os.path.join(manifest.root, manifest.questions_file))
                ]
            except WizardError as exc:
                payload["questions_error"] = str(exc)
        return payload

    return JSONResponse({
        "eligible": [describe(m) for m in eligible],
        "ineligible": [{**describe(m), "reason": reason}
                       for m, reason in rejected],
        "errors": [{"source": s, "message": msg} for s, msg in errors],
        "conflicts": [{"resource": c.resource, "packages": list(c.packages),
                       "message": c.message()}
                      for c in check_conflicts(eligible)],
    })


@app.post("/api/install/start")
async def api_install_start(config: InstallConfig) -> JSONResponse:
    # Demo/preview mode: refuse to actually launch the install engine. Lets the
    # portal be served on a live network for inspection without risking a real
    # (destructive) install. Enabled via NIVUUS_PORTAL_DEMO=1.
    if os.environ.get("NIVUUS_PORTAL_DEMO") == "1":
        return JSONResponse(
            {"ok": False, "error": "Mode démo : installation désactivée "
             "(portail en lecture seule)."},
            status_code=403,
        )
    if runner.is_running():
        return JSONResponse({"ok": False, "error": "install already running"},
                            status_code=409)
    try:
        runner.start(config.model_dump())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True})


@app.get("/api/install/status")
async def api_install_status() -> JSONResponse:
    return JSONResponse(runner.status())


@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket) -> None:
    """Stream progress events: backlog first, then incremental tail."""
    await ws.accept()
    last_seq = 0
    try:
        while True:
            for event in events_since(last_seq):
                last_seq = max(last_seq, event.get("seq", 0))
                await ws.send_json(event)
                if event.get("level") in ("done", "error"):
                    return
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


# --------------------------------------------------------------------------- #
# Captive-portal detection (force the wizard to auto-open)                    #
# --------------------------------------------------------------------------- #
@app.get("/generate_204")        # Android
@app.get("/gen_204")
async def captive_android() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


@app.get("/hotspot-detect.html")  # iOS / macOS
@app.get("/library/test/success.html")
async def captive_apple() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


@app.get("/ncsi.txt")             # Windows
async def captive_windows_ncsi() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


@app.get("/connecttest.txt")
@app.get("/redirect")
@app.get("/canonical.html")       # Firefox/NetworkManager
async def captive_generic() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


# Static assets (mounted last so it doesn't shadow API routes).
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("NIVUUS_PORTAL_HOST", "0.0.0.0")
    port = int(os.environ.get("NIVUUS_PORTAL_PORT", "80"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
