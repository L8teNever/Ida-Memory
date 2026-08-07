"""Web-Dashboard fuer Ida-Memory: eine einzelne, in sich geschlossene HTML-Seite
(app/dashboard.html, komplett inline CSS/JS, kein Build-Schritt noetig) plus
ein paar schlanke JSON-Endpunkte, die dieselbe KnowledgeGraphManager-Instanz
wie die MCP-Tools benutzen (single source of truth, siehe app/server.py).

Laeuft auf derselben App/demselben Port wie der MCP-Endpunkt -- dieselbe
BearerAuthMiddleware (siehe app/auth.py) schuetzt automatisch auch diese
Routen mit, kein eigener Auth-Mechanismus noetig. Die Seite selbst liest das
?token= aus ihrer eigenen URL (im Browser per JS) und haengt es an jeden
eigenen API-Aufruf wieder an -- exakt dasselbe Prinzip wie beim MCP-Endpunkt.
"""

from __future__ import annotations

import pathlib

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from app.knowledge_graph import KnowledgeGraphManager

_DASHBOARD_HTML_PATH = pathlib.Path(__file__).parent / "dashboard.html"
_dashboard_html_cache: str | None = None


def _dashboard_html() -> str:
    global _dashboard_html_cache
    if _dashboard_html_cache is None:
        _dashboard_html_cache = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    return _dashboard_html_cache


def _graph(request: Request) -> KnowledgeGraphManager:
    return request.app.state.graph


async def dashboard_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_dashboard_html())


async def api_stats(request: Request) -> JSONResponse:
    return JSONResponse(_graph(request).stats())


async def api_entities(request: Request) -> JSONResponse:
    q = request.query_params
    try:
        offset = int(q.get("offset", "0") or 0)
        limit = int(q.get("limit", "50") or 50)
    except ValueError:
        return JSONResponse({"error": "invalid_params", "message": "offset/limit muessen Zahlen sein."}, status_code=400)
    ergebnis = _graph(request).list_entities(
        offset=offset, limit=limit, query=q.get("q", ""), entity_type=q.get("type", ""),
    )
    return JSONResponse(ergebnis)


async def api_entity_detail(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    ergebnis = _graph(request).entity_detail(name)
    if ergebnis is None:
        return JSONResponse(
            {"error": "not_found", "message": f"Entity '{name}' existiert nicht."}, status_code=404
        )
    return JSONResponse(ergebnis)


async def api_graph(request: Request) -> JSONResponse:
    q = request.query_params
    graph = _graph(request)
    focus = q.get("focus", "")

    if focus:
        try:
            depth = int(q.get("depth", "1") or 1)
        except ValueError:
            depth = 1
        ergebnis = graph.neighborhood(focus, depth=depth)
        if not ergebnis["entities"]:
            return JSONResponse(
                {"error": "not_found", "message": f"Entity '{focus}' existiert nicht."}, status_code=404
            )
        return JSONResponse(ergebnis)

    # Bewusst IMMER der komplette Graph, keine Deckelung auf die am
    # staerksten vernetzten Knoten -- ausdruecklich so gewuenscht ("wirklich
    # alles", nicht nur eine Teilmenge). top_connected()/die fruehere
    # Groessengrenze bleiben in knowledge_graph.py verfuegbar, falls das
    # doch mal wieder gebraucht wird, werden hier aber nicht mehr benutzt.
    ergebnis = graph.read_graph()
    ergebnis.pop("hinweis", None)
    return JSONResponse(ergebnis)


def register_dashboard_routes(app, graph: KnowledgeGraphManager) -> None:
    app.state.graph = graph
    app.add_route("/", dashboard_page, methods=["GET"])
    app.add_route("/api/stats", api_stats, methods=["GET"])
    app.add_route("/api/entities", api_entities, methods=["GET"])
    app.add_route("/api/entity/{name:path}", api_entity_detail, methods=["GET"])
    app.add_route("/api/graph", api_graph, methods=["GET"])
