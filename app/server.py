"""Ida-Memory MCP Server.

Ein gemeinsames, themenuebergreifendes Wissensgraph-Gedaechtnis, das mehrere
KIs gleichzeitig als eigenen MCP-Connector verbinden koennen -- ueber
Streamable HTTP, damit es (anders als der offizielle stdio-only Referenz-
server @modelcontextprotocol/server-memory) remote ueber einen Cloudflare
Tunnel erreichbar ist. Der Endpunkt ist per Shared-Secret-Token abgesichert
(siehe app/auth.py).

Die neun Tools entsprechen bewusst 1:1 Name und Verhalten des offiziellen
Referenzservers (siehe app/knowledge_graph.py) -- jede KI, die das Muster
schon kennt, kann diesen Server ohne Umlernen benutzen. Einzige bewusste
Erweiterung gegenueber dem Original: search_nodes deckelt die Trefferzahl
(SEARCH_RESULT_LIMIT) und read_graph warnt bei grossem Bestand -- damit das
Gedaechtnis auch nach Jahren und tausenden Eintraegen noch wenig Tokens pro
Abfrage kostet, statt bei jeder Anfrage alles durchzureichen.
"""

from __future__ import annotations

import logging

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from app.auth import BearerAuthMiddleware
from app.config import load_settings
from app.knowledge_graph import KnowledgeGraphError, KnowledgeGraphManager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ida-memory")

settings = load_settings()
graph = KnowledgeGraphManager(settings.memory_file_path, settings.search_result_limit)

mcp = FastMCP(
    "Ida-Memory",
    instructions=(
        "Gemeinsames Wissensgraph-Gedaechtnis fuer mehrere KIs. Entities haben "
        "einen Namen, einen entityType und eine Liste von observations (kurze "
        "Fakten als Text). Relations verbinden zwei Entities gerichtet "
        "(from -> to) mit einem relationType. "
        "WICHTIG fuer Token-Effizienz: read_graph gibt den KOMPLETTEN Graphen "
        "zurueck und wird mit wachsendem Bestand teuer -- fuer normale Fragen "
        "immer zuerst search_nodes (Volltextsuche) oder open_nodes (gezielt "
        "bekannte Namen) benutzen, read_graph nur wenn wirklich alles gebraucht "
        "wird. search_nodes begrenzt die Trefferzahl automatisch und sagt, wenn "
        "es mehr Treffer gibt, die durch eine praezisere Suche sichtbar werden."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
)


@mcp.tool()
def create_entities(entities: list[dict]) -> list[dict]:
    """Legt neue Entities im Wissensgraph an.

    entities: Liste von {"name": str, "entityType": str, "observations": [str, ...]}.
    Namen, die es schon gibt, werden uebersprungen (kein Fehler, kein
    Ueberschreiben) -- Rueckgabe sind nur die tatsaechlich neu angelegten.
    """
    return graph.create_entities(entities)


@mcp.tool()
def create_relations(relations: list[dict]) -> list[dict]:
    """Legt neue, gerichtete Relations zwischen bestehenden Entities an.

    relations: Liste von {"from": str, "to": str, "relationType": str},
    relationType idiomatisch im Aktiv-Present (z.B. "arbeitet_bei",
    "kennt", "gehoert_zu"). Exakte Duplikate werden uebersprungen.
    """
    return graph.create_relations(relations)


@mcp.tool()
def add_observations(observations: list[dict]) -> list[dict]:
    """Haengt neue Beobachtungen an bestehende Entities an (kein Ueberschreiben).

    observations: Liste von {"entityName": str, "contents": [str, ...]}.
    Bereits vorhandene, identische Texte werden uebersprungen. Fehler, wenn
    eine genannte Entity nicht existiert -- vorher mit create_entities anlegen.
    """
    try:
        return graph.add_observations(observations)
    except KnowledgeGraphError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def delete_entities(entityNames: list[str]) -> str:
    """Entfernt Entities und alle Relations, die sie referenzieren.

    entityNames: Liste der zu loeschenden Entity-Namen. Unbekannte Namen
    werden ignoriert.
    """
    graph.delete_entities(entityNames)
    return "Entities geloescht."


@mcp.tool()
def delete_observations(deletions: list[dict]) -> str:
    """Entfernt einzelne Beobachtungstexte von Entities, ohne die Entity selbst zu loeschen.

    deletions: Liste von {"entityName": str, "observations": [str, ...]}.
    Unbekannte Entities oder Texte werden ignoriert.
    """
    graph.delete_observations(deletions)
    return "Beobachtungen geloescht."


@mcp.tool()
def delete_relations(relations: list[dict]) -> str:
    """Entfernt Relations.

    relations: Liste von {"from": str, "to": str, "relationType": str} --
    muss exakt uebereinstimmen, um geloescht zu werden.
    """
    graph.delete_relations(relations)
    return "Relations geloescht."


@mcp.tool()
def read_graph() -> dict:
    """Gibt den KOMPLETTEN Wissensgraphen zurueck (alle Entities, alle Relations).

    Teuer bei grossem Bestand -- nur benutzen, wenn wirklich ein
    Gesamtueberblick gebraucht wird. Fuer gezielte Fragen search_nodes oder
    open_nodes benutzen.
    """
    return graph.read_graph()


@mcp.tool()
def search_nodes(query: str) -> dict:
    """Durchsucht Entity-Namen, -Typen und Beobachtungstexte nach query (Volltextsuche, Gross-/Kleinschreibung egal).

    Gibt passende Entities zurueck plus alle Relations, an denen mindestens
    eine der gefundenen Entities beteiligt ist. Die Trefferzahl ist begrenzt
    (siehe SEARCH_RESULT_LIMIT); wenn es mehr Treffer gibt, steht das im
    Ergebnis -- dann die Suche praezisieren statt read_graph zu benutzen.
    """
    return graph.search_nodes(query)


@mcp.tool()
def open_nodes(names: list[str]) -> dict:
    """Gibt genau die genannten Entities zurueck (per Name), plus alle Relations,
    an denen mindestens eine davon beteiligt ist.

    names: Liste bekannter Entity-Namen (z.B. aus einem vorherigen
    search_nodes-Ergebnis). Unbekannte Namen werden ignoriert.
    """
    return graph.open_nodes(names)


async def healthz(request):
    return JSONResponse({"status": "ok"})


def build_app():
    app = mcp.streamable_http_app()
    app.add_route("/healthz", healthz, methods=["GET"])
    app.add_middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app


def main() -> None:
    app = build_app()
    log.info(
        "Ida-Memory MCP Server startet auf %s:%s (Endpunkt: /mcp, Health: /healthz, Speicher: %s)",
        settings.mcp_host,
        settings.mcp_port,
        settings.memory_file_path,
    )
    # access_log=False: uvicorn wuerde sonst jede Request-Zeile inkl. vollem
    # Pfad loggen -- und damit ein per ?token= mitgeschicktes MCP_AUTH_TOKEN
    # im Klartext in die Docker-Logs schreiben.
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
