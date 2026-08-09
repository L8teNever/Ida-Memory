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

Zwei getrennte Container, ein gemeinsames Datenvolume -- MCP-Endpunkt und
Web-Dashboard laufen bewusst auf unterschiedlichen Ports/Hostnamen, nicht
im selben Prozess:

```
KI/Client 1 (z.B. claude.ai)   --https-->  Cloudflare Tunnel (memory.deine-domain.de)
KI/Client 2 (z.B. eine Routine) --https-->        |
KI/Client 3 (z.B. Claude Desktop) --https-->      v
                                       10.7.0.1:4568 auf deinem Server
                                               |
                                               v
                                Docker-Container "ida-memory-mcp"  (schreibt)
                                               |
                                               v
                                    /data/memory.jsonl (Docker-Volume)
                                               ^
                                               | (liest nur)
                                Docker-Container "ida-memory-dashboard"
                                               ^
                                       10.7.0.1:4571 auf deinem Server
                                               |
Browser --(Cloudflare-Access-Login)--> Cloudflare Tunnel (idamemory.deine-domain.de) --+
```

Beide Container binden ihren Port **nur auf die Docker-Netzwerk-Gateway-IP
(`10.7.0.1`)**, wie bei den anderen Ida-*-Containern auf demselben Server --
von außen nicht direkt erreichbar, nur über den bereits laufenden
`cloudflared`-Prozess. Die beiden Hostnamen sind aber **unterschiedlich
abgesichert**: `memory.*` (MCP) verlangt bei jeder Anfrage das geheime
`MCP_AUTH_TOKEN` (App-Ebene, damit auch KI-Clients ohne interaktiven Login
sich verbinden können); `idamemory.*` (Dashboard) hat auf App-Ebene **gar
keinen** Auth-Zwang mehr, sondern wird ausschließlich durch eine davor
geschaltete **Cloudflare-Access-Application** geschützt (Login per
E-Mail-Code) -- siehe Abschnitt "Web-Dashboard" unten. Diese
Access-Application ist deshalb sicherheitskritisch, nicht optional.

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

Zwei Ingress-Regeln, eine pro Container (analog zu den anderen
Ida-*-Projekten):

```yaml
ingress:
  - hostname: memory.deine-domain.de
    service: http://10.7.0.1:4568
  - hostname: idamemory.deine-domain.de
    service: http://10.7.0.1:4571
  - service: http_status:404
```

(Bzw. im Zero-Trust-Dashboard unter Public Hostname eintragen -- dabei wird
i.d.R. automatisch auch der passende DNS-CNAME-Eintrag angelegt.) Danach
`cloudflared` neu laden.

**Für `idamemory.*` zusätzlich zwingend:** eine Access-Application
einrichten, siehe Abschnitt "Web-Dashboard" weiter unten -- ohne die ist
der komplette Wissensgraph für jeden im Internet lesbar, der den Hostnamen
kennt.

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
| `projekt_info_setzen(name, status, beschreibung, geplant, entityType)` | Legt strukturierte Projekt-Infos an oder aktualisiert sie. Siehe unten. |
| `projekte_liste()` | Alle Entities mit hinterlegten Projekt-Infos, alphabetisch, Status auf einen Blick. |

## Projekt-Tracking: Übergabe-Notiz zwischen KI-Sitzungen

Über die normalen `observations` hinaus (freier Text, unstrukturiert) gibt
es für Entities vom Typ "Projekt" ein optionales, strukturiertes Feld
`project: {status, beschreibung, geplant, aktualisiert_am}`, gesetzt über
`projekt_info_setzen`. Zweck: **eine andere KI -- oder dieselbe KI in einer
neuen, kontextlosen Sitzung -- soll allein durch dieses Feld verstehen, wo
die Arbeit an einem Projekt gerade steht, ohne den Nutzer erneut fragen zu
müssen, was schon umgesetzt wurde.** Das ist bewusst kein Dashboard-Feature
-- der Weg dorthin ist der MCP-Connector, genau wie bei allen anderen Tools:

- **`status`**: kurzer aktueller Stand, freier Text (z.B. "Geplant",
  "In Entwicklung", "Aktiv/Fertig", "Pausiert", "Archiviert").
- **`beschreibung`**: was im Projekt **bereits umgesetzt ist und
  funktioniert** -- konkret genug, dass eine neue Sitzung direkt weiß,
  worauf sie aufbaut, nicht nur eine Ein-Satz-Idee.
- **`geplant`**: der **nächste konkrete Schritt** bzw. die Roadmap.
- **`aktualisiert_am`**: wird automatisch bei jedem Aufruf gesetzt (UTC).

`projekt_info_setzen` ändert nur die tatsächlich übergebenen Felder (fehlende
= unverändert lassen), legt die Entity bei Bedarf neu an (Standard-Typ
"Projekt") und sollte **nach jeder inhaltlich relevanten Änderung** am
Projekt erneut aufgerufen werden, nicht nur einmalig beim Projektstart --
sonst veraltet die Übergabe-Notiz und der eigentliche Zweck geht verloren.

Das `project`-Feld ist Teil der normalen Entity und taucht deshalb überall
automatisch mit auf, wo Entities zurückgegeben werden -- `search_nodes`,
`open_nodes`, `read_graph` -- eine KI muss `projekte_liste` also gar nicht
kennen, um beim Nachschlagen eines bekannten Projektnamens den Stand zu
sehen; `projekte_liste()` ist nur die schnelle Übersicht über alle Projekte
auf einmal, ohne jedes einzeln nachschlagen zu müssen.

## Web-Dashboard

Neben den MCP-Tools gibt es ein Browser-Dashboard, um den Wissensgraphen
selbst anzusehen -- ohne Umweg über eine KI. Läuft als **eigener Container**
(`ida-memory-dashboard`, `app/dashboard_server.py`) auf einem **eigenen
Port** (`DASHBOARD_PORT`, Standard 4571) und damit auch einem eigenen
Cloudflare-Hostnamen -- bewusst getrennt vom MCP-Endpunkt, nicht im selben
Prozess:

```
https://idamemory.deine-domain.de/
```

**Absicherung bewusst anders als beim MCP-Endpunkt:** kein `MCP_AUTH_TOKEN`
in der URL -- der Dashboard-Prozess selbst hat gar keinen App-Level-Auth
mehr (siehe Sicherheitshinweis im Docstring von `app/dashboard_server.py`).
Stattdessen haengt vor dem Hostnamen eine **Cloudflare-Access-Application**
mit einer Policy, die nur eine bestimmte E-Mail-Adresse per Login-Code
durchlaesst -- einmal einloggen, danach merkt sich der Browser das fuer die
konfigurierte `session_duration` (z.B. 30 Tage), ganz ohne einen langen
Token manuell einzutippen. Einrichtung: Zero Trust Dashboard -> Access ->
Applications -> Add an application -> Self-hosted, Domain =
`idamemory.deine-domain.de`, Policy mit Include-Regel `E-Mail` = deine
Adresse. **Wichtig:** dieser Hostname darf niemals ohne eine solche
Access-Application live gehen -- sonst waere der komplette Wissensgraph
oeffentlich lesbar.

- **Liste**: durchsuchbar, nach Entity-Typ filterbar, seitenweise geladen
  (nicht alles auf einmal) -- Klick auf eine Karte öffnet die Detailansicht
  mit allen Beobachtungen und Verknüpfungen.
- **Graph**: interaktive Knoten-Kanten-Ansicht (Maus/Rad oder Zwei-Finger-
  Pinch zum Zoomen, ziehbar, Knoten einzeln verschiebbar), zeigt immer den
  **kompletten** Graphen, keine Deckelung auf eine Teilmenge -- eigene,
  synchron vorberechnete Force-Layout-Engine (kein sichtbares "Einpendeln"
  beim Laden). Knotengröße richtet sich nach Anzahl Verbindungen, nicht
  nach Beobachtungsanzahl. Klick auf einen Knoten hebt ihn und seine
  direkten Nachbarn hervor, der Rest wird gedimmt; "Erweitern" laedt
  gezielt die Nachbarschaft eines Knotens nach (fuer Fokus-Exploration statt
  alles auf einmal zu betrachten), zusätzlich nach Entity-Typ filterbar wie
  in der Liste.
- Passt sich responsiv an Desktop, Tablet und Handy an (Material-3-Design,
  folgt automatisch dem System-Farbschema hell/dunkel).

Backend-seitig (`app/dashboard.py`, neue Lesefunktionen in
`app/knowledge_graph.py`: `list_entities`, `entity_detail`, `neighborhood`,
`top_connected`, `stats`) liest dieselbe `/data/memory.jsonl` wie die
MCP-Tools -- eine einzige Quelle der Wahrheit, kein zweiter Speicherpfad,
auch wenn es ein eigener Prozess/Container ist (`app/dashboard_server.py`
teilt sich das Docker-Volume mit `app/server.py`, schreibt aber nie selbst
hinein -- `_save()` schreibt atomar, damit ein gleichzeitiger Lesevorgang
nie eine unvollständige Datei zu sehen bekommt). Alles clientseitig in
`app/dashboard.html` (eine Datei, kein Build-Schritt, kein externes
JS-Framework, eigene Canvas-Graph-Engine).

## Lokal testen ohne Cloudflare

```bash
docker compose up -d
curl -H "Authorization: Bearer $MCP_AUTH_TOKEN" http://10.7.0.1:4568/healthz
curl http://10.7.0.1:4571/healthz
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
