"""Ida-Memory Web-Dashboard -- eigener Prozess/Container, eigener Port,
getrennt vom MCP-Endpunkt (app/server.py). Liest dieselbe
/data/memory.jsonl (dasselbe Docker-Volume) wie der MCP-Server, aber rein
lesend -- von hier aus wird nie geschrieben, nur der MCP-Server legt
Entities/Relations an.

SICHERHEIT -- bewusst KEIN MCP_AUTH_TOKEN/BearerAuthMiddleware hier: dieser
Prozess ist absichtlich komplett offen fuer alles, was ihn ueber
10.7.0.1:<DASHBOARD_PORT> erreicht. Das ist nur sicher, WEIL vor dem
oeffentlichen Hostname (idamemory.<domain>) eine Cloudflare-Access-
Application mit einer Policy haengt, die nur die eigene E-Mail-Adresse per
Login-Code durchlaesst -- die eigentliche Zugriffskontrolle passiert also
am Cloudflare-Edge, bevor eine Anfrage hier ueberhaupt ankommt, nicht mehr
in dieser Anwendung. Diese Datei/dieser Port darf NIEMALS ueber einen
Tunnel-Hostnamen ohne davorgeschaltete Access-Application erreichbar
gemacht werden -- sonst waere der komplette Wissensgraph oeffentlich
lesbar. Der MCP-Endpunkt (app/server.py) behaelt seinen eigenen
Token-Zwang, weil claude.ai/Routinen keinen interaktiven E-Mail-Login-Flow
durchlaufen koennen.
"""

from __future__ import annotations

import logging

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from app.config import load_settings
from app.dashboard import register_dashboard_routes
from app.knowledge_graph import KnowledgeGraphManager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ida-memory-dashboard")

settings = load_settings()
graph = KnowledgeGraphManager(settings.memory_file_path, settings.search_result_limit)


async def healthz(request):
    return JSONResponse({"status": "ok"})


def build_app():
    app = Starlette()
    app.add_route("/healthz", healthz, methods=["GET"])
    register_dashboard_routes(app, graph)
    return app


def main() -> None:
    app = build_app()
    log.info(
        "Ida-Memory Dashboard startet auf %s:%s (Dashboard: /, Health: /healthz, Speicher: %s) "
        "-- ungeschuetzt auf App-Ebene, verlaesst sich auf eine vorgeschaltete Cloudflare-Access-Application",
        settings.mcp_host,
        settings.dashboard_port,
        settings.memory_file_path,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.dashboard_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
