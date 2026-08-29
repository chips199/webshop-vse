# UML-Diagramme

Quelldateien (Mermaid) der Diagramme aus `docs/architecture.md`. Die
gerenderten Diagramme selbst sind zur besseren Lesbarkeit weiterhin inline in
`architecture.md` eingebettet; hier liegen sie zusaetzlich als eigenstaendige
`.mmd`-Dateien gemaess der geforderten Repository-Struktur (Aufgabenblatt
Abschnitt 6.1).

| Datei | Diagrammtyp | Abschnitt in architecture.md |
| --- | --- | --- |
| `systemkontext.mmd` | Systemkontext-Diagramm | 2. Systemkontext |
| `komponenten.mmd` | Komponentendiagramm | 3. Komponenten |
| `sequenz-happy-path.mmd` | Sequenzdiagramm (Happy Path) | 5. Happy-Path-Sequenz |
| `sequenz-fehlerszenario-lager-nicht-verfuegbar.mmd` | Sequenzdiagramm (Fehlerszenario) | 6.1 Lager nicht verfuegbar |
| `sequenz-fehlerszenario-zahlung-abgelehnt.mmd` | Sequenzdiagramm (Fehlerszenario) | 6.2 Zahlung abgelehnt |
| `sequenz-fehlerszenario-rechnung-fehlgeschlagen.mmd` | Sequenzdiagramm (Fehlerszenario) | 6.3 Invoice-Service nicht erreichbar |
| `sequenz-fehlerszenario-asynchrone-zahlung.mmd` | Sequenzdiagramm (Fehlerszenario) | 6.4 Asynchrone Zahlung |
| `sequenz-fehlerszenario-warehouse-commit-fehlgeschlagen.mmd` | Sequenzdiagramm (zusaetzlich, nicht in der Arbeitsgrundlage gefordert) | - |

Jede `.mmd`-Datei laesst sich z.B. mit dem [Mermaid Live Editor](https://mermaid.live)
oder `mmdc` (mermaid-cli) unabhaengig rendern.
