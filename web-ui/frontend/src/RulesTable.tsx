import { useEffect, useMemo, useState } from "react";
import {
  fetchParallelSuggestions,
  fetchRules,
  fetchRuleSummary,
  fetchSystems,
  ParallelSuggestion,
  Rule,
  RuleDomain,
  rejectParallel,
  RuleSummary,
  setParallelGroup,
  System,
  unrejectParallel,
} from "./api";
import type { View, ViewParams } from "./App";
import RuleDetailDrawer from "./RuleDetail";
import { FAMILY_LABEL, FamilyMarks, SideMarks, useVdomShown, VdomChip } from "./refs";
import { ActionBadge, Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

// Seitengrösse: Server filtert nach Art/System/Suche, der Rest wird mit "Weitere laden" geholt.
const PAGE = 500;

// Plattformen mit Regeln je Art (Filter "System" zeigt nur passende Quellsysteme)
const RULE_PLATFORMS: Record<RuleDomain, string> = { firewall: "fortigate", aci_contract: "cisco_aci" };

function ObjCell({ items }: { items: string[] }) {
  if (!items.length) return <Dash />;
  const shown = items.slice(0, 4);
  return (
    <div className="objs">
      {shown.map((s, i) => (
        <span className="mono" key={i}>
          {s}
        </span>
      ))}
      {items.length > shown.length && <span className="faint">+{items.length - shown.length} weitere</span>}
    </div>
  );
}

function ZoneChip({ zone }: { zone: string | null }) {
  return zone ? <span className="chip">{zone}</span> : <Dash />;
}

// NAT einer Policy: SNAT (Pool bzw. Interface-IP) und VIPs (DNAT) - Klick öffnet die NAT-Sicht
export function NatChips({ r, onGo }: { r: Rule; onGo?: (v: View, p?: ViewParams) => void }) {
  if (!r.nat) return null;
  const open = (name: string) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onGo?.("nat", { q: name });
  };
  return (
    <div className="chips" style={{ marginTop: 3 }}>
      {r.nat.vips.map((v) => (
        <button key={v} className="chip local linkchip" title="DNAT (VIP) – in NAT / VIPs öffnen" onClick={open(v)}>
          VIP {v}
        </button>
      ))}
      {r.nat.snat &&
        (r.nat.pools.length ? (
          r.nat.pools.map((pool) => (
            <button key={pool} className="chip info linkchip" title="Source-NAT-Pool – in NAT / VIPs öffnen" onClick={open(pool)}>
              SNAT {pool}
            </button>
          ))
        ) : (
          <span className="chip info" title="Source-NAT auf die IP des ausgehenden Interfaces">
            SNAT Interface-IP
          </span>
        ))}
    </div>
  );
}

// ACI-Contract: Contract + Subject, darunter ACI-Tenant und Scope (auch in der Applikations-Doku)
export function NameCell({
  r,
  onGo,
  onPair,
}: {
  r: Rule;
  onGo?: (v: View, p?: ViewParams) => void;
  onPair?: (id: number) => void;
}) {
  if (r.aci) {
    const pref = r.aci.kind === "preferred_group";
    return (
      <>
        <span className="chip local" title="Cisco-ACI-Contract">
          ACI
        </span>{" "}
        {r.aci.kind === "taboo" && (
          <>
            <span className="chip crit" title="Taboo-Contract: verbietet den Verkehr zu den geschützten EPGs">
              Taboo
            </span>{" "}
          </>
        )}
        {r.aci.kind === "preferred_group" && (
          <>
            <span className="chip info" title="Preferred Group: Mitglieder der VRF sprechen frei miteinander">
              Preferred Group
            </span>{" "}
          </>
        )}
        <b>{pref ? `VRF ${(r.aci.contract ?? r.name).replace(/^Preferred Group /, "")}` : (r.aci.contract ?? r.name)}</b>
        <div className="faint">
          {[!pref && r.aci.subject && `Subject ${r.aci.subject}`, r.aci.tenant && `Tenant ${r.aci.tenant}`, r.aci.scope]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </>
    );
  }
  return (
    <>
      <b>{r.name}</b>
      {r.section_label && <div className="faint">{r.section_label}</div>}
      <NatChips r={r} onGo={onGo} />
      <FamilyMarks r={r} onPair={onPair} />
    </>
  );
}

// Parallele Firewalls (Verbund, 0.18.0): gleiche Policy auf weiteren Firewalls, Abweichung
export function ParallelChips({ r }: { r: Rule }) {
  const p = r.parallel;
  if (!p) return null;
  return (
    <div className="chips" style={{ marginTop: 2 }}>
      {(p.same ?? []).map((n) => (
        <span key={n} className="chip ok" title={`gleiche Policy auf ${n} (Verbund ${p.group}) – zusammengefasst`}>
          + {n}
        </span>
      ))}
      {(p.differs ?? []).map((n) => (
        <span key={n} className="chip warn" title={`auf ${n} anders konfiguriert – siehe Findings`}>
          ≠ {n}
        </span>
      ))}
      {(p.missing ?? []).map((n) => (
        <span key={n} className="chip warn" title={`fehlt auf ${n} (Verbund ${p.group})`}>
          fehlt auf {n}
        </span>
      ))}
      {p.lead && (
        <span className="chip warn" title={`weicht von ${p.lead} ab: ${(p.fields ?? []).join(", ")}`}>
          ≠ {p.lead}: {(p.fields ?? []).join(", ")}
        </span>
      )}
    </div>
  );
}

// Vorschlag: Firewalls, die parallel aussehen (Session-Sync, gleiche Policies) – Admin fasst zusammen
function ParallelHint({ tenantId, isAdmin, onDone }: { tenantId: number; isAdmin: boolean; onDone: () => void }) {
  const [items, setItems] = useState<ParallelSuggestion[]>([]);
  const [showRejected, setShowRejected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    fetchParallelSuggestions(tenantId, isAdmin)
      .then(setItems)
      .catch(() => setItems([]));
  }, [tenantId, isAdmin, reload]);
  const open = items.filter((s) => !s.rejected);
  const rejected = items.filter((s) => s.rejected);
  if (!open.length && !rejected.length) return null;
  const act = (action: Promise<unknown>, changed: boolean) => {
    setBusy(true);
    setError(null);
    action
      .then(() => {
        setReload((n) => n + 1);
        if (changed) onDone();
      })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  return (
    <>
      <ErrorBox error={error} prefix="Verbund" />
      {open.map((s) => (
        <Note key={s.system_ids.join("-")}>
          <b>{s.names.join(" und ")}</b> sehen parallel aus ({s.reasons.join(", ")}) – ihre Regeln stehen doppelt
          in der Liste.{" "}
          {isAdmin ? (
            <>
              <button
                className="btn btn-sm btn-primary"
                disabled={busy}
                onClick={() => act(setParallelGroup(tenantId, s.group, s.system_ids), true)}
              >
                Als Verbund „{s.group}“ zusammenfassen
              </button>{" "}
              <button
                className="btn btn-sm"
                disabled={busy}
                title="nicht parallel – wird nicht mehr vorgeschlagen"
                onClick={() => act(rejectParallel(tenantId, { system_ids: s.system_ids }), false)}
              >
                Ablehnen
              </button>
            </>
          ) : (
            <span className="faint">Ein Admin kann sie als Verbund zusammenfassen oder ablehnen.</span>
          )}
        </Note>
      ))}
      {isAdmin && rejected.length > 0 && (
        <div className="faint" style={{ margin: "0 0 10px" }}>
          <button className="linkbtn" onClick={() => setShowRejected((v) => !v)}>
            {rejected.length} abgelehnte{rejected.length === 1 ? "r" : ""} Vorschlag{rejected.length === 1 ? "" : "e"}
          </button>
          {showRejected &&
            rejected.map((s) => (
              <div key={s.system_ids.join("-")} style={{ marginTop: 4 }}>
                {s.names.join(" und ")} – nicht parallel{" "}
                <button
                  className="linkbtn"
                  disabled={busy}
                  onClick={() => act(unrejectParallel(tenantId, s.system_ids), false)}
                >
                  wieder vorschlagen
                </button>
              </div>
            ))}
        </div>
      )}
    </>
  );
}

export default function RulesTable({
  tenantId,
  tenantLabel,
  params = {},
  onGo,
  isAdmin = false,
  customer = false,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // object= / nat= / q= aus einem Link (Objekt, NAT, Finding)
  onGo?: (v: View, p?: ViewParams) => void;
  isAdmin?: boolean;
  customer?: boolean; // Kunden-Benutzer: keine Verbund-Vorschläge (ADR 0018)
}) {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [systems, setSystems] = useState<System[]>([]);
  const [summary, setSummary] = useState<RuleSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const object = params.object ?? "";
  const objectId = params.object_id ? Number(params.object_id) : null;
  const nat = params.nat ?? "";
  const net = params.net ?? ""; // Regeln eines Subnetzes (Link aus Objekte → Subnetze)
  const [netAny, setNetAny] = useState(true);

  const [q, setQ] = useState(params.q ?? "");
  const [domain, setDomain] = useState<RuleDomain | "">("");
  const [system, setSystem] = useState("");
  const [srcZone, setSrcZone] = useState("");
  const [dstZone, setDstZone] = useState("");
  const [action, setAction] = useState("");
  const [family, setFamily] = useState("");
  const [vdom, setVdom] = useState(""); // FortiGate-VDOM (nur mit mehreren VDOMs)
  const [pair, setPair] = useState(""); // IPv4/IPv6-Paar: similar = offen zum Bestätigen
  const [parallel, setParallel] = useState(""); // Verbund: differs | missing
  const [reload, setReload] = useState(0); // nach einer Paar-Entscheidung neu laden
  const [onlyFindings, setOnlyFindings] = useState(false);
  const [routable, setRoutable] = useState(false); // Suche nach einer IP: unmögliche ausblenden
  const [impossible, setImpossible] = useState<number | null>(null);
  // Maus über einer Regel hebt ihr IPv4/IPv6-Gegenstück hervor
  const [pairHover, setPairHover] = useState<number | null>(null);
  // Link aus Findings/Objekten auf genau eine Regel (#regeln?rule=<id>): Detail direkt öffnen
  const [selected, setSelected] = useState<number | null>(params.rule ? Number(params.rule) : null);

  useEffect(() => {
    setSystem("");
    setVdom("");
    fetchSystems(tenantId)
      .then(setSystems)
      .catch(() => setSystems([]));
    fetchRuleSummary(tenantId)
      .then(setSummary)
      .catch(() => setSummary(null));
  }, [tenantId]);

  const query = useMemo(
    () => ({
      q,
      tenantId,
      domain,
      systemId: system ? Number(system) : null,
      object,
      objectId,
      nat,
      net,
      netAny,
      srcZone,
      dstZone,
      action,
      family,
      vdom,
      pair,
      parallel,
      routable,
      limit: PAGE,
      reload,
    }),
    [
      q,
      tenantId,
      domain,
      system,
      object,
      objectId,
      nat,
      net,
      netAny,
      srcZone,
      dstZone,
      action,
      family,
      vdom,
      pair,
      parallel,
      routable,
      reload,
    ],
  );

  // Suche und alle Filter wertet der Orchestrator aus (über alle Regeln, nicht nur die erste Seite).
  // Die Suche findet auch Gruppenmitglieder, Werte/IP im Netz, Services, Zonen, EPGs und NAT.
  useEffect(() => {
    const timer = setTimeout(() => {
      setRules(null);
      setError(null);
      fetchRules(query)
        .then((page) => {
          setRules(page.rules);
          setTotal(page.total);
          setImpossible(page.impossible ?? null);
        })
        .catch((e) => {
          setError(e);
          setRules([]);
          setTotal(0);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const loadMore = () => {
    if (!rules) return;
    setMore(true);
    fetchRules({ ...query, offset: rules.length })
      .then((page) => {
        setRules([...rules, ...page.rules]);
        setTotal(page.total);
      })
      .catch(setError)
      .finally(() => setMore(false));
  };

  const systemName = (id: number) => systems.find((s) => s.id === id)?.name ?? `#${id}`;
  const hasGroups = systems.some((s) => s.parallel_group);
  const ruleSystems = systems.filter((s) =>
    domain ? s.platform === RULE_PLATFORMS[domain] : Object.values(RULE_PLATFORMS).includes(s.platform),
  );

  // Auswahl der Zonen/Aktionen aus den Kennzahlen aller Regeln (nicht nur der geladenen Seite)
  const zoneNames = (rows?: [string, number][]) => (rows ?? []).map(([z]) => z).filter((z) => z && z !== "–").sort();
  const srcZones = zoneNames(summary?.by_src_zone);
  const dstZones = zoneNames(summary?.by_dst_zone);
  const actions = Object.keys(summary?.by_action ?? {}).sort();
  // VDOMs (ADR 0019): Filter und „root“ nur, wenn es mehrere gibt
  const vdoms = summary?.by_vdom ?? [];
  const multiVdom = vdoms.length > 1; // Filter nur mit mehreren VDOMs
  const vdomShown = useVdomShown();

  const filtered = (rules ?? []).filter((r) => !onlyFindings || r.num_unmatched > 0);
  const loaded = rules?.length ?? 0;
  // ACI: Quelle = Consumer-EPGs, Ziel = Provider-EPGs
  const srcHead = domain === "aci_contract" ? "Consumer" : domain === "firewall" ? "Quelle" : "Quelle / Consumer";
  const dstHead = domain === "aci_contract" ? "Provider" : domain === "firewall" ? "Ziel" : "Ziel / Provider";

  return (
    <>
      <PageHead title="Regeln & Contracts">
        {rules === null
          ? "Lade…"
          : `${total} Einträge für „${tenantLabel}“ – Firewall-Policies und ACI-Contracts, read-only eingelesen und gegen die Source of Truth aufgelöst.`}
      </PageHead>
      {tenantId !== null && !customer && (
        <ParallelHint
          tenantId={tenantId}
          isAdmin={isAdmin}
          onDone={() => {
            setReload((n) => n + 1);
            fetchSystems(tenantId)
              .then(setSystems)
              .catch(() => undefined);
          }}
        />
      )}

      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Suche: Objekt, IP, DNS-Name, NAT, Zone…"
            title="Sucht in allem: Regel, Objekte und Gruppenmitglieder, IP (auch im Netz), Netz/CIDR (Regeln, die sich mit dem Netz überschneiden), DNS-Name aus dem IPAM, Service, Zone, EPG, NAT, Kommentar. Mehrere Wörter = alle müssen vorkommen."
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        {net && (
          <span className="chip local" title="Regeln, deren Quelle oder Ziel sich mit dem Subnetz überschneidet (auch über Gruppen)">
            Subnetz {net}
            <button className="linkbtn" title="Filter entfernen" onClick={() => onGo?.("regeln")}>
              ✕
            </button>
          </span>
        )}
        {net && (
          <button
            className={`toggle ${netAny ? "on" : ""}`}
            title="Regeln mit any als Quelle bzw. Ziel einbeziehen"
            onClick={() => setNetAny((v) => !v)}
          >
            <span className="dot" />
            inkl. any
          </button>
        )}
        {(object || nat) && (
          <span className="chip local" title="aus einem Link – Regeln, die dieses Objekt bzw. diesen NAT-Eintrag verwenden">
            {object ? `Objekt ${object}${params.family ? ` (${FAMILY_LABEL[params.family] ?? params.family})` : ""}` : `NAT ${nat}`}
            <button className="linkbtn" title="Filter entfernen" onClick={() => onGo?.("regeln")}>
              ✕
            </button>
          </span>
        )}
        <select
          className="sel"
          value={domain}
          onChange={(e) => {
            setDomain(e.target.value as RuleDomain | "");
            setSystem("");
          }}
        >
          <option value="">Art: alle</option>
          <option value="firewall">Firewall-Regeln</option>
          <option value="aci_contract">ACI-Contracts</option>
        </select>
        <select className="sel" value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">System: alle</option>
          {ruleSystems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select className="sel" value={srcZone} onChange={(e) => setSrcZone(e.target.value)}>
          <option value="">Quell-Zone: alle</option>
          {srcZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={dstZone} onChange={(e) => setDstZone(e.target.value)}>
          <option value="">Ziel-Zone: alle</option>
          {dstZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Aktion: alle</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select className="sel" value={family} onChange={(e) => setFamily(e.target.value)} title="Adressfamilie der Policy">
          <option value="">Familie: alle</option>
          <option value="v4">IPv4</option>
          <option value="v6">IPv6</option>
          <option value="dual">IPv4+IPv6</option>
        </select>
        {multiVdom && (
          <select className="sel" value={vdom} onChange={(e) => setVdom(e.target.value)} title="FortiGate-VDOM der Policy">
            <option value="">VDOM: alle</option>
            {vdoms.map(([v, n]) => (
              <option key={v} value={v}>
                {v} ({n})
              </option>
            ))}
          </select>
        )}
        <select
          className="sel"
          value={pair}
          onChange={(e) => setPair(e.target.value)}
          title="IPv4/IPv6-Paare: „ähnlich“ = vorgeschlagen, noch nicht bestätigt"
        >
          <option value="">Paar: alle</option>
          <option value="similar">ähnlich (offen)</option>
          <option value="confirmed">bestätigt</option>
          <option value="same">gleich</option>
          <option value="any">mit Paar</option>
          <option value="none">ohne Paar</option>
        </select>
        {hasGroups && (
          <select
            className="sel"
            value={parallel}
            onChange={(e) => setParallel(e.target.value)}
            title="Parallele Firewalls (Verbund): gleiche Policies sind zusammengefasst"
          >
            <option value="">Verbund: alle</option>
            <option value="differs">abweichend</option>
            <option value="missing">fehlt auf einer Firewall</option>
          </select>
        )}
        <button className={`toggle ${onlyFindings ? "on" : ""}`} onClick={() => setOnlyFindings((v) => !v)}>
          <span className="dot y" />
          nur mit Findings
        </button>
        {impossible !== null && (impossible > 0 || routable) && (
          <button
            className={`toggle ${routable ? "on" : ""}`}
            onClick={() => setRoutable((v) => !v)}
            title="Regeln, die die gesuchte IP nach dem Routing ihrer Firewall nicht sehen können (Ein- bzw. Ausgang passt nicht)"
          >
            <span className="dot" />
            {impossible} routingtechnisch unmögliche ausblenden
          </button>
        )}
        <span className="count">
          {filtered.length} Treffer{total > loaded ? ` · ${loaded} von ${total} geladen` : ""}
        </span>
      </div>

      <ErrorBox error={error} prefix="Regeln laden" />

      <div className="card">
        <div className="card-body scroll viewport">
          {rules === null ? (
            <Loading>Lade Regeln…</Loading>
          ) : filtered.length === 0 ? (
            <Empty title={rules.length ? "Keine Treffer" : "Noch keine Regeln"}>
              {rules.length
                ? "Filter oder Suche anpassen."
                : "Sobald ein Quellsystem eingelesen wurde, erscheinen hier die Regeln und Contracts."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Aktion</th>
                  <th>System</th>
                  <th>Quell-Zone</th>
                  <th>Ziel-Zone</th>
                  <th>{srcHead}</th>
                  <th>{dstHead}</th>
                  <th>Dienste</th>
                  <th title="nicht aufgelöste Objekte"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr
                    key={r.id}
                    className={`clickable${pairHover === r.id ? " pair-hl" : ""}`}
                    onClick={() => setSelected(r.id)}
                    onMouseEnter={() => setPairHover(r.pair?.rule_id ?? null)}
                    onMouseLeave={() => setPairHover(null)}
                  >
                    <td className="mono">{r.id}</td>
                    <td>
                      <NameCell r={r} onGo={onGo} onPair={setSelected} />
                    </td>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td>
                      {systemName(r.source_system_id)}
                      {vdomShown(r.source_system_id, r.vdom) && (
                        <div>
                          <VdomChip vdom={r.vdom} always />
                        </div>
                      )}
                      <ParallelChips r={r} />
                      {r.relevance?.state === "impossible" && (
                        <div>
                          <span className="chip crit" title={r.relevance.text}>
                            für {q.trim()} nicht möglich
                          </span>
                        </div>
                      )}
                    </td>
                    <td>
                      <ZoneChip zone={r.src_zone} />
                    </td>
                    <td>
                      <ZoneChip zone={r.dst_zone} />
                    </td>
                    <td>
                      <SideMarks r={r} side="src" />
                      <ObjCell items={r.sources} />
                    </td>
                    <td>
                      <SideMarks r={r} side="dst" />
                      <ObjCell items={r.destinations} />
                    </td>
                    <td>
                      <SideMarks r={r} side="service" />
                      <ObjCell items={r.service_names?.length ? r.service_names : []} />
                    </td>
                    <td>
                      {r.num_unmatched > 0 ? (
                        <span className="chip warn" title="nicht aufgelöste Objekte">
                          {r.num_unmatched}
                        </span>
                      ) : (
                        <span className="chip ok">✓</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {rules !== null && total > loaded && (
          <div className="card-foot">
            {loaded} von {total} geladen – Suche, Art oder System eingrenzen oder{" "}
            <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
              {more ? "Lade…" : `weitere ${Math.min(PAGE, total - loaded)} laden`}
            </button>
          </div>
        )}
      </div>

      {selected !== null && (
        <RuleDetailDrawer
          ruleId={selected}
          systemName={systemName}
          onClose={() => setSelected(null)}
          onGo={onGo}
          onRule={setSelected}
          onChanged={() => setReload((n) => n + 1)}
          isAdmin={isAdmin}
        />
      )}
    </>
  );
}
