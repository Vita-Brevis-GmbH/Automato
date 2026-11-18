# ADR 0071 — Regel-Zuordnung nach echtem Treffer (ersetzt die Zuordnung nach Adressüberdeckung)

## Status

**Angenommen** (2026-11-18) – zur Abnahme durch den Anwender.

Anwender 2026-11-18:

- „Da ich nicht alle Geräte im Dev habe, würde ich gerne zuerst die Wegsuche (Routing) und Firewall
  Rule zuordnung zu Applikation den Algorithmus kontrollieren lassen … Es soll absolut der Wahrheit
  entsprechen, wie das Netzwerk in Echt funktioniert.“
- „Direkt korrigieren oder noch besser, wenn es schneller geht, einen neuen Algorithmus schreiben.“
- **„Ich bin für ersetzen, da das alte Verhalten nicht immer richtig lag.“**

Ergänzt ADR 0019 (Regeln auf dem Weg), ADR 0043 (Regelwerk der Applikations-Doku), ADR 0048 (keine
stillen Grenzen), ADR 0049 (Datenstand nach Inhalt), ADR 0037 (Doku als Stand); Schwester-ADR 0070
(Weg-Suche nach echtem Geräteverhalten). Prüfung „B“ mit den Abweichungen B-1 bis B-13:
`orchestrator/tests/test_fable_regeln.py`, `collectors/tests/test_fable_regeln.py`.

## Kontext

Die Doku einer Applikation fragte bisher „welche Regel **nennt** die Adresse“
(`rule_index.covers`: Bereiche aller Firewalls des Mandanten, eine SQL-Abfrage je Adresse). Das
echte Gerät fragt „welche Regel **trifft** den Verkehr“:

- ohne Reihenfolge: eine Regel hinter einer breiteren stand als Zugriff da, obwohl sie nie Verkehr
  trifft (B-6);
- ohne Negation: `srcaddr-negate`/`dstaddr-negate` wurde wie eine genannte Adresse gelesen – die
  Regel stand beim Server, den sie ausnimmt (B-4), und fehlte dort, wo sie wirkt (B-5);
- ohne Weg: eine Regel, die das System nennt, blieb von jeder Firewall des Mandanten stehen, auch
  wenn der Verkehr dort nie vorbeikommt (B-12, Stichprobe 1); im Regelwerk 1 wurde der Weg nur für
  unspezifische Regeln geprüft;
- FQDN-Objekte wurden nie aufgelöst (B-7).

Dazu Sonderfälle im Abgleich je Hop (`policy_check`, `aci_check`) und in den Collectors
(B-1 bis B-3, B-8 bis B-11).

## Entscheid

### 1. Ersetzen – für alle Regelwerk-Profile (Ausnahme zur Profil-Regel)

Die neue Zuordnung **ersetzt** die alte ganz: kein Umschalter, kein Schattenbetrieb im Produkt
(Vergleich alt/neu nur im Messskript). Welche Regel den Verkehr eines Systems wirklich trifft, gilt
für **jedes** Regelwerk-Profil; die Profile entscheiden weiter nur, was die Doku davon zeigt.
Bedingungen, Wirkungen und Reihenfolge der eingebauten Profile bleiben unverändert (ADR 0043) – sie
wirken auf die richtige Menge.

Das ist eine **bewusste Ausnahme** zur Regel „eine neue Regel bzw. eine neue Reihenfolge = neues
Profil“ (Anwender 2026-10-30): die Regel gilt weiter für das, was die Doku *zeigt*; was eine Regel
*trifft*, ist keine Einstellung, sondern das Verhalten des Geräts. Drei Dinge gelten deshalb fest,
ausserhalb der Profile (`appdoc_filter`):

| Fest | Wirkung | Wo |
|---|---|---|
| Firewall der Regel nicht auf dem Weg | nicht in der Doku, **gezählt** unter „nicht auf dem Weg“ (`off_path` bzw. `off_path_specific`) – auch wenn das Profil die Bedingung ausgeschaltet hat (Regelwerk 1: `off_path_specific`); schaltet ein Profil sie ein, gilt seine Wirkung; direkt zugeordnete Regeln und Ausnahmen des Profils bleiben | `appdoc_filter.HARD`, `decide` |
| Regel für das System ganz überdeckt | kein Zugriff: nur noch Treffer unter „Regeln & Contracts“ mit Kennzeichen „überdeckt durch Policy X“ (`_shadowed`); direkt zugeordnete bleiben in der Kategorie | `applications.build_doc`, `appdoc_filter.SHADOWED` |
| Weg nicht bekannt | die Regel bleibt, **markiert** „Weg nicht geprüft“ (`_path_unchecked`) und gezählt (`summary.path_unchecked`) | `appdoc_path.OnPath.unknown` |

### 2. Die Zuordnung (`store/rule_match.py`)

- **Wirksame Menge je Policy und Seite** (`Matcher.covers`): die Bereiche der Policy aus dem
  Regelindex; eine **negierte** Seite ist ihr **Komplement** (trifft das System, wenn kein Bereich
  es berührt – Bezug „nicht …“, Art `subnet`; nennt sie das System, trifft sie es nicht).
  **FQDN**-Objekte treffen die Adressen, auf die der Name laut IPAM auflöst (Bezug „… (FQDN laut
  IPAM)“); ohne IPAM-Eintrag bleibt es wie bisher (kein Treffer, im Abgleich „möglich“).
- **Wirksamer Rest** (`Matcher.shadow`): je Firewall/VDOM die Policies in Listen-Reihenfolge
  (`Rule.sequence`). Eine Policy ist für ein System überdeckt, wenn frühere **sichere** Policies
  allen Verkehr des Systems, den sie trifft, schon entscheiden: jede enthält Ein- und Ausgang (nach
  Namen, `any` enthält alles), die Gegenstellen (Intervalle je Familie) und das System; **zusammen**
  decken sie alle Dienste (Protokoll und Ports als Intervalle, `ALL`). Die Aktion der früheren ist
  gleich – auch eine erlaubende beendet den Abgleich.
- **Sicher** heisst: eingeschaltet, ohne Benutzer/Gruppen, Zeitplan `always`, beide Seiten als
  Adressen bekannt (kein FQDN, Internet Service, Land, keine Negation), Dienst bekannt (kein
  `service-negate`, keine unbekannten Protokolle), Interfaces gelesen. **Was nicht prüfbar ist,
  überdeckt nie** – sonst verschwänden echte Regeln.
- **Äquivalenzklassen** inkl. Dienst: gerechnet je Adressklasse des Systems auf der Firewall
  (`policy_check.Checker.addr_class`, ADR 0048) – Systeme zwischen denselben Grenzen aller
  Bereiche haben dasselbe Ergebnis; je Klasse ein Durchlauf über die Policies, die das System
  sicher treffen, mit Fächern (/16) für die Gegenstellen und Intervallen für die Dienste.
- **Breit:** nennt die überdeckte Regel das System, steht an ihrer Stelle die **real treffende**
  Policy davor – in derselben Kategorie, mit Gegenstelle und Dienst des gemeinten Zugriffs,
  gekennzeichnet „breit – trifft anstelle von …“ (`_broad`). Ihre Aktion sagt, ob der Zugriff geht
  (auch eine Sperre wird so gezeigt).
- **Weg als harte Bedingung** (`appdoc_path.OnPath`, für jede Regel): der Weg zwischen den
  Gegenstellen der Regel und dem System wie bei den Verbindungen (`app_paths.connection_trace`,
  Wege-Speicher ADR 0048); nur Firewalls auf dem Weg tragen bei. Gegenstelle `any` bzw. negiert:
  der Weg ist nicht bestimmt, es gilt das Routing der Firewall (`relevance`, ADR 0019). Regeln, die
  das System über eine VIP bzw. einen Load Balancer erfassen, stehen auf der Firewall, die
  umschreibt – sie liegt auf dem Weg.
- **Regel abseits des Wegs, die das System nennt:** nicht in der Doku, aber für die Verbindungen
  gemerkt (`doc.off_path`) – der Befund „Regel nie gebraucht“ (`app_rule_unused`, 0.107.0) bleibt.
- **Im Speicher je Datenstand** (ADR 0049): Policies, Index und Ergebnisse je Adressklasse liegen
  im Wege-Speicher des Mandanten (`path_store`), keine SQL-Abfrage je Adresse. `RuleIpRange` bleibt
  für Suche, Objekt-Detail und Fussabdruck.

### 3. Abgleich je Hop wie das Gerät

| Nr. | Gerät | Jetzt |
|---|---|---|
| B-1 | `internet-service-negate` | trifft alles ausserhalb der Liste: private Adressen sicher, öffentliche „möglich“ |
| B-2 | Policy mit VIP als Ziel | trifft nur Verkehr, den **diese** VIP an diesem Hop umgeschrieben hat – nie Verkehr direkt zur internen Adresse bzw. zu Servern eines Load Balancers dahinter |
| B-3 | DNAT vor dem Policy-Abgleich | Adressobjekte vergleichen am VIP-Hop mit dem Ziel **nach** dem DNAT; ein Objekt der externen IP trifft nicht |
| – | erlaubende Policy mit `all` bzw. interner Adresse für Verkehr zur VIP | ohne Central NAT nicht belegt: **„möglich“** („Ziel (VIP ohne VIP-Objekt)“), nie still „trifft“; mit Central NAT sicher |
| – | Central NAT | `hop["central_nat"]` (True, False, None = nicht bekannt) je FortiGate/VDOM setzt die Weg-Suche (ADR 0070); der Abgleich vergleicht dann wie FortiOS mit der internen Adresse |
| B-8 | ACI-Vorrang | Priorität der Zoning-Rules (kleinere Zahl gewinnt, bei gleicher die Sperre): Taboo 5, EPG–EPG 7 (9 mit `ip/any`), EPG–vzAny 13 (15), vzAny–EPG 14 (16), vzAny–vzAny 17 (21), Preferred Group 20 – nicht mehr „Sperre zuerst“ |
| B-10 | ACI-Filter `action=deny` | gelesen samt `priorityOverride` (level1/2/3 = 17 / 13 bzw. 14 / 7), als sperrende Regel |
| – | ACI VRF „unenforced“, Intra-EPG-Isolation | gelesen (`epg.vrf_unenforced`, `epg.isolated`, Migration `0098`): unenforced = erlaubt ohne Contract; Isolation = gleiche EPG nur mit Contract der EPG mit sich selbst; nicht gelesen (NULL bis zum nächsten Lauf) = Urteil wie bisher, im Ergebnis ausgewiesen (`unchecked`) |
| B-9 | `action ipsec` | erlaubt (Policy-basiertes IPsec) |
| B-11 | Adress- und Dienstgruppen | ganz aufgelöst (beliebig tief, Schutz vor Schleifen) |
| B-13 | Umleitung 80→443 am Load Balancer | Weg-Suche (ADR 0070) |

### 4. Befund „Regel überdeckt“

Neue Prüfung `app/checks/kern/regel_ueberdeckt.py` (ADR 0039), Typ `rule_shadowed`: eine Policy,
deren **ganzen** Verkehr frühere sichere Policies entscheiden (`Matcher.shadowed_rules`, in jeder
Familie, in der sie Adressen nennt). Warnung, wenn die überdeckende anders entscheidet (Sperre vor
Freigabe), sonst Hinweis; gleiche Policies paralleler Firewalls einmal; je Mandant abschaltbar.

### 5. Stand der Doku und Datenstand

- **Fingerabdruck** (ADR 0037): die Fassung der Zuordnung (`rule_match.VERSION`) steht im Abdruck
  – jeder Stand rechnet **einmal** neu, danach stabil. Eine künftige Änderung der Zuordnung erhöht
  die Fassung.
- **Datenstand** (ADR 0049): die neuen Felder der EPGs zählen über ihren Inhalt (Teil `epgs`);
  Referenzquellen (IPAM für FQDN) wirken wie bisher auf alle Applikationen; eine Regel mit negierter
  Seite gilt im Fussabdruck wie `any` (jede Applikation, deren Wege über ihr Gerät gehen).

## Grenzen (ausgewiesen, nie zu Lasten einer echten Regel)

- Überdeckung, die sich nur aus mehreren Policies mit je einem **Teil der Gegenstellen bzw. der
  Interfaces** ergibt, wird nicht erkannt – die Regel bleibt (zu viel gezeigt, nie zu wenig).
  Zonen und ihre Interfaces gelten nur nach Namen als enthalten.
- Policies mit einer VIP im Ziel werden nicht als überdeckt gerechnet und überdecken nicht.
- Gegenstelle `any`/negiert: kein Weg je Gegenstelle, nur das Routing der Firewall (wie bisher).
- Herstellerdoku war bei der Prüfung nur über Suchtreffer lesbar; wo ein Verhalten nicht belegt
  ist (VIP mit `all`, negierter Internet Service für öffentliche Adressen), gilt „möglich“.
- Weiter nicht gelesen: NGFW Policy-Mode, Proxy-, Local-in- und DoS-Policies, Quellports und
  ICMP-Typ der Dienste; Wildcard-Adressen bleiben „nicht prüfbar“.

## Folgen

- Aus der Doku **verschwinden**: Regeln von Firewalls abseits des Wegs (gezählt), Regeln mit
  negierter Seite, die das System nennen. **Nur noch Treffer** (nicht mehr in den Kategorien):
  ganz überdeckte Regeln. **Neu**: Regeln mit negierter Seite, die das System treffen; Regeln über
  FQDN laut IPAM; die real treffende breite Policy an der Stelle der überdeckten; Befund „Regel
  überdeckt“; Kennzeichen „überdeckt“, „breit“, „Weg nicht geprüft“.
- Urteile je Hop ändern sich bei VIPs (ohne VIP-Objekt „abhängig“ statt „erlaubt“), negiertem
  Internet Service und im ACI (Vorrang).
- In den Verbindungen tragen die Regeln die Kennzeichen der Doku (`rules[].marks`) neben ihrem
  Zustand auf dem Weg („vorher entscheidet …“, „nicht prüfbar“).

## Messung

`scripts/bench_fable_regeln.py 4000 4000 2000` (SQLite, 4 Kerne, eine FortiGate, 12 Zonen, 54 085
Bereiche im Regelindex; im Mittel 420 Policies je Adresse, davon rund 400 über `all`):

| Schritt | bis 1.21 | ADR 0071 |
|---|---|---|
| Zuordnung je Adresse | 18,5 ms (`rule_index.covers`, SQL) | 2,2 ms (`rule_match.covers`, im Speicher) |
| Aufbau im Speicher | – | 1,1 s einmal je Datenstand (mit den Policies des Abgleichs geteilt) |
| wirksamer Rest | nicht gerechnet | 3,7 ms je Adressklasse und Seite (kalt), danach 0,015 ms je Regel und Adresse |
| ganz überdeckte Policies (Befund) | – | 0,6 s je Firewall mit 4000 Policies |
| Doku je Applikation (5 Hosts, 20 Applikationen nacheinander) | 239 ms | 229 ms |
| Abgleich je Fluss (warm) | 0,040 ms | 0,040 ms |

Hochrechnung 800 Applikationen mit je 5 Adressen: Zuordnung 74 s → rund 9 s, dazu der wirksame
Rest (höchstens rund 30 s, einmal je Datenstand und Adressklasse); die Doku insgesamt rund 3 Minuten
wie bisher – sie rechnet jetzt zusätzlich Reihenfolge und Weg je Regel. **Nicht gemessen:** mehrere
Firewalls mit gelesenem Routing (der Weg wird jetzt für jede Regel geprüft, nicht nur für
unspezifische – mehr Wege, die der Wege-Speicher je Datenstand teilt) und Postgres.
