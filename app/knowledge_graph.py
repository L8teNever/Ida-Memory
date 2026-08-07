"""Wissensgraph-Speicher -- Verhalten 1:1 nachgebaut nach dem offiziellen
MCP-Referenzserver @modelcontextprotocol/server-memory
(github.com/modelcontextprotocol/servers/blob/main/src/memory/index.ts,
Zeile fuer Zeile gegengeprueft), nur als Python/Streamable-HTTP-Server statt
Node/stdio -- damit er ueber einen Cloudflare Tunnel von mehreren KIs
gleichzeitig als gemeinsames Gedaechtnis genutzt werden kann (stdio-Server
koennen das grundsaetzlich nicht, die sprechen nur lokal per Pipe).

Modell: Entities (name, entityType, observations[]) und gerichtete Relations
(from, to, relationType) dazwischen. Speicherformat identisch zum Original:
JSONL (eine JSON-Zeile pro Entity/Relation) mit einem "type"-Feld.

Effizienz bei grossem Bestand (Ziel: auch bei zehntausenden Eintraegen noch
guenstig): search_nodes/open_nodes geben nur Treffer zurueck, nie den ganzen
Graphen. search_nodes deckelt zusaetzlich (anders als im Original) die
Trefferzahl pro Aufruf (SEARCH_RESULT_LIMIT) -- ein zu allgemeiner Suchbegriff
kann sonst bei vielen Eintraegen trotzdem hunderte Treffer und damit sehr
viele Tokens verursachen. read_graph() bleibt fuer den Sonderfall "wirklich
alles" verfuegbar, meldet aber ab einer gewissen Groesse einen Hinweis,
lieber gezielt zu suchen.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LARGE_GRAPH_WARNING_THRESHOLD = 100


class KnowledgeGraphError(RuntimeError):
    """Fehler, die 1:1 als verständliche Meldung an den MCP-Client zurückgehen sollen."""


class KnowledgeGraphManager:
    def __init__(self, path: str, search_result_limit: int) -> None:
        self._path = Path(path)
        self._search_result_limit = search_result_limit
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- Persistenz (JSONL, wie im Original) -----------------------------

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"entities": [], "relations": []}

        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("type") == "entity":
                entities.append(
                    {
                        "name": item["name"],
                        "entityType": item["entityType"],
                        "observations": item["observations"],
                    }
                )
            elif item.get("type") == "relation":
                relations.append(
                    {
                        "from": item["from"],
                        "to": item["to"],
                        "relationType": item["relationType"],
                    }
                )
        return {"entities": entities, "relations": relations}

    def _save(self, graph: dict[str, list[dict[str, Any]]]) -> None:
        """Schreibt atomar (temp-Datei + os.replace) statt direkt in die
        Zieldatei -- seit das Dashboard als eigener Prozess/Container
        dieselbe Datei nur lesend mitbenutzt (siehe app/dashboard_server.py),
        koennte ein Leser sonst mitten in einem Schreibvorgang eine
        unvollstaendige/kaputte Zeile zu sehen bekommen."""
        lines = [
            json.dumps({"type": "entity", **entity}, ensure_ascii=False)
            for entity in graph["entities"]
        ] + [
            json.dumps({"type": "relation", **relation}, ensure_ascii=False)
            for relation in graph["relations"]
        ]
        tmp_path = self._path.with_suffix(self._path.suffix + f".tmp-{os.getpid()}")
        try:
            tmp_path.write_text("\n".join(lines), encoding="utf-8")
            os.replace(tmp_path, self._path)
        except BaseException:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    # -- Schreiboperationen (Semantik 1:1 wie im Original) ----------------

    def create_entities(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Legt neue Entities an. Namen, die es schon gibt, werden still
        uebersprungen (kein Fehler) -- Rueckgabe sind nur die tatsaechlich
        neu angelegten."""
        with self._lock:
            graph = self._load()
            existing_names = {e["name"] for e in graph["entities"]}
            new_entities = [e for e in entities if e["name"] not in existing_names]
            graph["entities"].extend(new_entities)
            self._save(graph)
            return new_entities

    def create_relations(self, relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Legt neue Relations an. Exakte Duplikate (gleiches from+to+relationType)
        werden still uebersprungen."""
        with self._lock:
            graph = self._load()

            def exists(r: dict[str, Any]) -> bool:
                return any(
                    er["from"] == r["from"]
                    and er["to"] == r["to"]
                    and er["relationType"] == r["relationType"]
                    for er in graph["relations"]
                )

            new_relations = [r for r in relations if not exists(r)]
            graph["relations"].extend(new_relations)
            self._save(graph)
            return new_relations

    def add_observations(self, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Haengt Beobachtungen an bestehende Entities an (kein Ueberschreiben).
        Bereits vorhandene, identische Beobachtungstexte werden uebersprungen.
        Fehler, wenn eine genannte Entity nicht existiert."""
        with self._lock:
            graph = self._load()
            results = []
            for obs in observations:
                entity = next(
                    (e for e in graph["entities"] if e["name"] == obs["entityName"]), None
                )
                if entity is None:
                    raise KnowledgeGraphError(
                        f"Entity '{obs['entityName']}' existiert nicht -- "
                        "erst mit create_entities anlegen."
                    )
                new_contents = [c for c in obs["contents"] if c not in entity["observations"]]
                entity["observations"].extend(new_contents)
                results.append({"entityName": obs["entityName"], "addedObservations": new_contents})
            self._save(graph)
            return results

    def delete_entities(self, entity_names: list[str]) -> None:
        """Entfernt Entities und alle Relations, die sie referenzieren.
        Unbekannte Namen werden ignoriert (kein Fehler)."""
        with self._lock:
            graph = self._load()
            names = set(entity_names)
            graph["entities"] = [e for e in graph["entities"] if e["name"] not in names]
            graph["relations"] = [
                r for r in graph["relations"] if r["from"] not in names and r["to"] not in names
            ]
            self._save(graph)

    def delete_observations(self, deletions: list[dict[str, Any]]) -> None:
        """Entfernt einzelne Beobachtungstexte von genannten Entities.
        Unbekannte Entities/Texte werden ignoriert (kein Fehler)."""
        with self._lock:
            graph = self._load()
            for deletion in deletions:
                entity = next(
                    (e for e in graph["entities"] if e["name"] == deletion["entityName"]), None
                )
                if entity is not None:
                    to_remove = set(deletion["observations"])
                    entity["observations"] = [
                        o for o in entity["observations"] if o not in to_remove
                    ]
            self._save(graph)

    def delete_relations(self, relations: list[dict[str, Any]]) -> None:
        """Entfernt exakt passende Relations (from+to+relationType)."""
        with self._lock:
            graph = self._load()

            def matches(r: dict[str, Any], delrel: dict[str, Any]) -> bool:
                return (
                    r["from"] == delrel["from"]
                    and r["to"] == delrel["to"]
                    and r["relationType"] == delrel["relationType"]
                )

            graph["relations"] = [
                r for r in graph["relations"] if not any(matches(r, d) for d in relations)
            ]
            self._save(graph)

    # -- Leseoperationen ---------------------------------------------------

    def read_graph(self) -> dict[str, Any]:
        """Gibt den KOMPLETTEN Graphen zurueck -- teuer bei vielen Eintraegen.
        Nur nutzen, wenn wirklich alles gebraucht wird; sonst search_nodes
        oder open_nodes."""
        with self._lock:
            graph = self._load()
            result: dict[str, Any] = dict(graph)
            if len(graph["entities"]) > _LARGE_GRAPH_WARNING_THRESHOLD:
                result["hinweis"] = (
                    f"Der Graph hat {len(graph['entities'])} Entities -- fuer "
                    "gezielte Fragen ist search_nodes oder open_nodes "
                    "guenstiger als jedes Mal alles zu lesen."
                )
            return result

    def search_nodes(self, query: str) -> dict[str, Any]:
        """Volltextsuche (Groß-/Kleinschreibung egal) ueber Entity-Namen,
        -Typen und Beobachtungstexte. Liefert passende Entities plus alle
        Relations, an denen mindestens eine der gefundenen Entities beteiligt
        ist (so lassen sich Verbindungen zu Knoten außerhalb der Trefferliste
        entdecken).

        Deckelt die Trefferzahl auf search_result_limit (Default 30) --
        anders als im Originalserver, damit ein zu allgemeiner Suchbegriff bei
        großem Bestand nicht hunderte Treffer und damit unnoetig viele Tokens
        verursacht. Ist die Liste gedeckelt, steht das im Ergebnis, damit
        gezielter nachgefragt werden kann."""
        with self._lock:
            graph = self._load()
            q = query.lower()
            matching_entities = [
                e
                for e in graph["entities"]
                if q in e["name"].lower()
                or q in e["entityType"].lower()
                or any(q in o.lower() for o in e["observations"])
            ]

            total_gefunden = len(matching_entities)
            limited_entities = matching_entities[: self._search_result_limit]
            limited_names = {e["name"] for e in limited_entities}
            filtered_relations = [
                r
                for r in graph["relations"]
                if r["from"] in limited_names or r["to"] in limited_names
            ]

            result: dict[str, Any] = {
                "entities": limited_entities,
                "relations": filtered_relations,
            }
            if total_gefunden > len(limited_entities):
                result["hinweis"] = (
                    f"{total_gefunden} Treffer gefunden, nur die ersten "
                    f"{len(limited_entities)} angezeigt -- Suchbegriff "
                    "praezisieren, um den Rest zu sehen."
                )
            return result

    def open_nodes(self, names: list[str]) -> dict[str, Any]:
        """Gibt genau die genannten Entities zurueck (unbekannte Namen werden
        ignoriert) plus alle Relations, an denen mindestens eine davon beteiligt
        ist. Bewusst ODER statt UND (wie search_nodes): mit UND wuerden
        Relations von einer angefragten zu einer nicht angefragten Entity
        stillschweigend verschwinden -- man koennte die Verbindungen eines
        Knotens dann nur noch sehen, indem man den ganzen Graphen liest."""
        with self._lock:
            graph = self._load()
            wanted = set(names)
            filtered_entities = [e for e in graph["entities"] if e["name"] in wanted]
            filtered_names = {e["name"] for e in filtered_entities}
            filtered_relations = [
                r
                for r in graph["relations"]
                if r["from"] in filtered_names or r["to"] in filtered_names
            ]
            return {"entities": filtered_entities, "relations": filtered_relations}

    # -- Nur fuers Web-Dashboard (app/dashboard.py) -------------------------
    #
    # Dieselbe Grundidee wie bei search_nodes: nie mehr zurueckgeben, als eine
    # Ansicht gerade wirklich braucht, damit das Dashboard auch bei einem
    # jahrelang gewachsenen Bestand mit tausenden Eintraegen noch schnell
    # bleibt -- Listen sind serverseitig paginiert, die Graph-Ansicht bekommt
    # gezielt eine Nachbarschaft statt immer den kompletten Graphen.

    def stats(self) -> dict[str, Any]:
        with self._lock:
            graph = self._load()
        typ_anzahl: dict[str, int] = {}
        for e in graph["entities"]:
            typ_anzahl[e["entityType"]] = typ_anzahl.get(e["entityType"], 0) + 1
        entity_types = sorted(
            ({"entityType": t, "count": c} for t, c in typ_anzahl.items()),
            key=lambda x: (-x["count"], x["entityType"]),
        )
        return {
            "entityCount": len(graph["entities"]),
            "relationCount": len(graph["relations"]),
            "entityTypes": entity_types,
        }

    def list_entities(
        self, offset: int = 0, limit: int = 50, query: str = "", entity_type: str = ""
    ) -> dict[str, Any]:
        """Paginierte Uebersichtsliste -- OHNE die Beobachtungstexte selbst (nur
        Name/Typ/Anzahl), damit eine Seite auch bei riesigem Bestand klein und
        schnell bleibt. query (optional) filtert wie search_nodes per
        Volltextsuche, entity_type (optional) exakt nach Typ."""
        with self._lock:
            graph = self._load()
        entities = graph["entities"]
        if query:
            q = query.lower()
            entities = [
                e
                for e in entities
                if q in e["name"].lower()
                or q in e["entityType"].lower()
                or any(q in o.lower() for o in e["observations"])
            ]
        if entity_type:
            entities = [e for e in entities if e["entityType"] == entity_type]

        entities_sorted = sorted(entities, key=lambda e: e["name"].lower())
        total = len(entities_sorted)
        offset = max(0, offset)
        limit = max(1, min(limit, 200))
        page = entities_sorted[offset : offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "entities": [
                {
                    "name": e["name"],
                    "entityType": e["entityType"],
                    "observationCount": len(e["observations"]),
                }
                for e in page
            ],
        }

    def entity_detail(self, name: str) -> dict[str, Any] | None:
        """Eine einzelne Entity komplett (inkl. Beobachtungen) plus ihre
        direkten Relations mit Richtung -- fuer die Detailansicht. None, wenn
        der Name nicht existiert."""
        with self._lock:
            graph = self._load()
        entity = next((e for e in graph["entities"] if e["name"] == name), None)
        if entity is None:
            return None
        connections = []
        for r in graph["relations"]:
            if r["from"] == name:
                connections.append({"name": r["to"], "relationType": r["relationType"], "direction": "out"})
            elif r["to"] == name:
                connections.append({"name": r["from"], "relationType": r["relationType"], "direction": "in"})
        entities_by_name = {e["name"]: e for e in graph["entities"]}
        for c in connections:
            verbunden = entities_by_name.get(c["name"])
            c["entityType"] = verbunden["entityType"] if verbunden else None
        return {"entity": entity, "connections": connections}

    def neighborhood(self, name: str, depth: int = 1, max_nodes: int = 80) -> dict[str, Any]:
        """Fuer die interaktive Graph-Ansicht: die Entity selbst plus alles,
        was bis zur angegebenen Tiefe darueber erreichbar ist -- nie der ganze
        Graph, damit Erkunden per Klick auch bei riesigem Bestand fluessig
        bleibt. max_nodes deckelt zusaetzlich hart, falls ein Knoten extrem
        vernetzt ist."""
        with self._lock:
            graph = self._load()
        entities_by_name = {e["name"]: e for e in graph["entities"]}
        if name not in entities_by_name:
            return {"entities": [], "relations": []}

        besucht = {name}
        grenze = {name}
        for _ in range(max(1, depth)):
            if len(besucht) >= max_nodes:
                break
            naechste_grenze: set[str] = set()
            for r in graph["relations"]:
                if r["from"] in grenze and r["to"] not in besucht:
                    naechste_grenze.add(r["to"])
                if r["to"] in grenze and r["from"] not in besucht:
                    naechste_grenze.add(r["from"])
            if not naechste_grenze:
                break
            uebrig = max_nodes - len(besucht)
            naechste_grenze = set(list(naechste_grenze)[:uebrig])
            besucht |= naechste_grenze
            grenze = naechste_grenze

        filtered_entities = [entities_by_name[n] for n in besucht if n in entities_by_name]
        filtered_relations = [
            r for r in graph["relations"] if r["from"] in besucht and r["to"] in besucht
        ]
        return {"entities": filtered_entities, "relations": filtered_relations}

    def top_connected(self, limit: int = 60) -> dict[str, Any]:
        """Die am staerksten vernetzten Entities als Startansicht fuer den
        Graphen bei grossem Bestand -- sinnvoller erster Blick als ein
        zufaelliger oder alphabetischer Ausschnitt. Gibt es noch keine oder
        kaum Relations, werden stattdessen einfach die ersten paar Entities
        genommen, damit die Ansicht trotzdem nicht leer ist."""
        with self._lock:
            graph = self._load()
        grad: dict[str, int] = {}
        for r in graph["relations"]:
            grad[r["from"]] = grad.get(r["from"], 0) + 1
            grad[r["to"]] = grad.get(r["to"], 0) + 1

        entities_by_name = {e["name"]: e for e in graph["entities"]}
        namen = {n for n, _ in sorted(grad.items(), key=lambda kv: -kv[1])[:limit]}
        if len(namen) < min(limit, len(graph["entities"])):
            for e in graph["entities"]:
                if len(namen) >= limit:
                    break
                namen.add(e["name"])

        filtered_entities = [entities_by_name[n] for n in namen if n in entities_by_name]
        filtered_relations = [
            r for r in graph["relations"] if r["from"] in namen and r["to"] in namen
        ]
        return {"entities": filtered_entities, "relations": filtered_relations}
