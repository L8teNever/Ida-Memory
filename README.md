# Ida-Memory

Ein eigenständiger MCP-Server (Model Context Protocol): ein gemeinsames,
themenübergreifendes Wissensgraph-Gedächtnis, das mehrere KIs/Connectors
gleichzeitig verbinden können (z.B. Claude Desktop, claude.ai, mehrere
claude.ai Routinen wie [Ida-Telegram](https://github.com/L8teNever/Ida-Telegram)).
Getrennt von Ida-Untis und Ida-Telegram -- ein eigener Container, ein eigenes
Repo, keine Abhängigkeit dazwischen.

Baut exakt auf dem Datenmodell und den Werkzeugnamen des offiziellen
MCP-Referenzservers [`@modelcontextprotocol/server-memory`](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)
auf -- dieselben neun Tools, dieselbe Semantik, dasselbe JSONL-Speicherformat.
Der Unterschied: das Original spricht nur stdio (lokal, ein Prozess pro
Client) und kann daher nicht als gemeinsamer Remote-Server für mehrere KIs
gleichzeitig gehostet werden. Ida-Memory ist eine native Python-Neuimplementierung
mit identischem Verhalten, aber über Streamable HTTP -- lauffähig als ein
einziger Container, den beliebig viele MCP-Clients gleichzeitig über einen
Cloudflare Tunnel verbinden.

## Datenmodell

- **Entity**: `{name, entityType, observations: [text, ...]}` -- ein Ding,
  das man sich merken will (Person, Projekt, Vorliebe, Fakt-Themenblock, ...).
- **Relation**: `{from, to, relationType}` -- eine gerichtete Beziehung
  zwischen zwei Entities (z.B. `"Ida" --arbeitet_an--> "Ida-Memory"`).

Gespeichert als JSONL (`memory.jsonl`, eine JSON-Zeile pro Entity/Relation)
in einem persistenten Docker-Volume.

## Warum das auch nach Jahren noch günstig bleibt

Der Verlauf ohne Limit hätte ein Problem: bringt ein Client jedes Mal den
kompletten Wissensstand mit, wird das mit wachsendem Bestand (im Lauf der
Jahre potenziell tausende Einträge) immer teurer -- viele Tokens für Fakten,
die für die aktuelle Frage gar nicht relevant sind. Deshalb:

- **`search_nodes`/`open_nodes` sind der Normalfall.** Sie geben nur
  Treffer zurück, nie den ganzen Graphen -- so bleibt jede Abfrage klein,
  unabhängig davon, wie groß das Gedächtnis insgesamt ist.
- **`search_nodes` deckelt zusätzlich die Trefferzahl** (`SEARCH_RESULT_LIMIT`,
  Standard 30 -- über dem offiziellen Original hinaus, das keine Grenze
  kennt). Ein zu allgemeiner Suchbegriff kann sonst bei großem Bestand
  trotzdem hunderte Treffer liefern. Gibt es mehr Treffer als angezeigt,
  steht das explizit in der Antwort, damit gezielter nachgefragt werden kann.
- **`read_graph`** (alles auf einmal) bleibt für den Sonderfall verfügbar,
  liefert aber ab einer gewissen Größe einen Warnhinweis, stattdessen gezielt
  zu suchen.

Damit bekommt eine KI immer *genug* Kontext, um eine Frage zu verstehen,
aber nicht *mehr* als nötig -- auch wenn aus zehn heutigen Fakten in ein paar
Jahren zehntausend geworden sind.

**Das gilt genauso beim Schreiben, nicht nur beim Lesen.** Der Server kann
selbst nicht beurteilen, was "wichtig" ist -- das entscheidet die
schreibende KI bei jedem Aufruf von `create_entities`/`create_relations`/
`add_observations`. Die Tool-Beschreibungen und die Server-`instructions`
weisen die verbundenen KIs deshalb ausdrücklich an:

- Nur dauerhaft nützliche, wirklich relevante Fakten speichern -- nicht
  jede beiläufige oder einmalige Kleinigkeit.
- Eine `observation` nur an die Entity hängen, zu der sie tatsächlich
  gehört -- nicht vorsorglich an mehrere.

Der Grund: ein mit Trivialkram vollgeschriebener Graph macht später auch
die bewusst begrenzten Suchergebnisse (`search_nodes`-Limit) weniger
brauchbar -- jeder unwichtige Eintrag konkurriert um einen der begrenzten
Plätze in der Trefferliste. Weniger, aber relevante Einträge sind besser
als möglichst viele.

## Architektur

```
KI/Client 1 (z.B. claude.ai)   --https-->  Cloudflare Tunnel (öffentliche Domain)
KI/Client 2 (z.B. eine Routine) --https-->        |
KI/Client 3 (z.B. Claude Desktop) --https-->      v
                                       127.0.0.1:4568 auf deinem Server
                                               |
                                               v
                                   Docker-Container "ida-memory-mcp"
                                               |
                                               v
                                    /data/memory.jsonl (Docker-Volume)
```

Der Container published seinen Port **nur auf `127.0.0.1`** -- von außen
nicht direkt erreichbar, nur über den bereits laufenden `cloudflared`-Prozess.
Zusätzlich verlangt der Server bei jeder Anfrage ein geheimes Token
(`MCP_AUTH_TOKEN`) -- alle verbundenen Clients teilen sich denselben Token
und damit dasselbe Gedächtnis.

## Voraussetzungen

- Docker + Docker Compose auf dem Server
- Ein bereits eingerichteter und verbundener Cloudflare Tunnel auf diesem Server

## 1. Einrichten, bauen, starten

```bash
git clone https://github.com/<dein-user>/Ida-Memory.git
cd Ida-Memory
cp .env.example .env
```

`.env` mit `MCP_AUTH_TOKEN` ausfüllen (z.B. `openssl rand -hex 32`).

Image bauen lassen: Bei jedem Push auf `main` baut
`.github/workflows/docker-publish.yml` das Image automatisch nach
`ghcr.io/<dein-user>/ida-memory:latest`. Einmalig auf öffentlich stellen
(GitHub -> Profil -> **Packages** -> `ida-memory` -> Package settings ->
Change visibility -> Public), damit `docker compose` es ohne Login ziehen kann.

```bash
docker compose pull
docker compose up -d
docker compose logs -f
```

## 2. An den bestehenden Cloudflare Tunnel anbinden

Analog zu Ida-Untis/Ida-Telegram, nur mit eigenem Hostname und Port 4568:

```yaml
ingress:
  - hostname: memory.deine-domain.de
    service: http://localhost:4568
  - service: http_status:404
```

(Bzw. im Zero-Trust-Dashboard unter Public Hostname eintragen.) Danach
`cloudflared` neu laden.

## 3. Als MCP-Connector hinzufügen

Für jede KI, die mitlesen/schreiben soll (z.B. claude.ai -> Einstellungen ->
Connectors -> Add custom connector), als URL:

```
https://memory.deine-domain.de/mcp?token=<MCP_AUTH_TOKEN>
```

Für die [Ida-Telegram](https://github.com/L8teNever/Ida-Telegram)-Routine:
denselben Connector zusätzlich bei den Konnektoren der Routine auswählen und
in den Routine-Anweisungen erwähnen, dass für themenübergreifendes Wissen
dieser Connector zu benutzen ist (steht im Ida-Telegram-README bereits als
Vorschlag für den Anweisungstext).

## Verfügbare MCP-Tools

Namen und Verhalten entsprechen 1:1 dem offiziellen Referenzserver:

| Tool | Zweck |
|---|---|
| `create_entities(entities)` | Legt neue Entities an (`name`, `entityType`, `observations`). Existierende Namen werden übersprungen. |
| `create_relations(relations)` | Legt neue, gerichtete Relations an (`from`, `to`, `relationType`). Duplikate werden übersprungen. |
| `add_observations(observations)` | Hängt Beobachtungen an eine bestehende Entity an. Fehler, wenn die Entity nicht existiert. |
| `delete_entities(entityNames)` | Löscht Entities und alle Relations, die sie referenzieren. |
| `delete_observations(deletions)` | Entfernt einzelne Beobachtungstexte, ohne die Entity zu löschen. |
| `delete_relations(relations)` | Löscht exakt passende Relations. |
| `read_graph()` | Gibt den **kompletten** Graphen zurück -- teuer bei großem Bestand, siehe oben. |
| `search_nodes(query)` | Volltextsuche über Namen/Typ/Beobachtungen, Trefferzahl begrenzt (`SEARCH_RESULT_LIMIT`). Normalfall für Abfragen. |
| `open_nodes(names)` | Gibt gezielt bekannte Entities zurück (z.B. aus einem vorherigen `search_nodes`-Ergebnis). |

## Lokal testen ohne Cloudflare

```bash
docker compose up -d
curl -H "Authorization: Bearer $MCP_AUTH_TOKEN" http://127.0.0.1:4568/healthz
```

## Troubleshooting

- **Container startet nicht**: `docker compose logs` -- meist fehlt
  `MCP_AUTH_TOKEN` in `.env`.
- **Claude/eine KI bekommt 401**: Token in Client-Konfiguration und `.env`
  vergleichen.
- **`add_observations` meldet "existiert nicht"**: Die Entity muss vorher
  über `create_entities` angelegt werden -- `add_observations` legt keine
  neuen Entities an.
- **Gedächtnis nach Neustart leer**: Prüfen, ob `docker compose down` (ohne
  `-v`) statt versehentlich `docker compose down -v` benutzt wurde -- `-v`
  löscht auch das benannte Volume `ida-memory-data`.
