"""Ida-Memory Web-Dashboard -- eigener Prozess/Container, eigener Port,
getrennt vom MCP-Endpunkt (app/server.py). Liest dieselbe
/data/memory.jsonl (dasselbe Docker-Volume) wie der MCP-Server, aber rein
lesend -- von hier aus wird nie geschrieben, nur der MCP-Server legt
Entities/Relations an. Dieselbe MCP_AUTH_TOKEN-Absicherung wie beim
MCP-Endpunkt (siehe app/auth.py), nur eben auf einem eigenen Port/eigenen
Hostname erreichbar statt auf demselben wie /mcp.
"""

from __future__ import annotations

import logging

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from app.auth import BearerAuthMiddleware
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
    app.add_middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app


def main() -> None:
    app = build_app()
    log.info(
        "Ida-Memory Dashboard startet auf %s:%s (Dashboard: /?token=..., Health: /healthz, Speicher: %s)",
        settings.mcp_host,
        settings.dashboard_port,
        settings.memory_file_path,
    )
    # access_log=False: uvicorn wuerde sonst jede Request-Zeile inkl. vollem
    # Pfad loggen -- und damit ein per ?token= mitgeschicktes MCP_AUTH_TOKEN
    # im Klartext in die Docker-Logs schreiben.
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.dashboard_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
