import { useEffect, useMemo, useState } from "react";
import {
  Application,
  assignAppRule,
  fetchApps,
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
import { L } from "./i18n";
import RuleDetailDrawer from "./RuleDetail";
import { FAMILY_LABEL, FamilyMarks, serviceNames, SideMarks, useVdomShown, VdomChip } from "./refs";
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
      {items.length > shown.length && (
        <span className="faint">{L(`+${items.length - shown.length} weitere`, `+${items.length - shown.length} more`)}</span>
      )}
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
        <button
          key={v}
          className="chip local linkchip"
          title={L("DNAT (VIP) – in NAT / VIPs öffnen", "DNAT (VIP) – open in NAT / VIPs")}
          onClick={open(v)}
        >
          VIP {v}
        </button>
      ))}
      {r.nat.snat &&
        (r.nat.pools.length ? (
          r.nat.pools.map((pool) => (
            <button
              key={pool}
              className="chip info linkchip"
              title={L("Source-NAT-Pool – in NAT / VIPs öffnen", "Source NAT pool – open in NAT / VIPs")}
              onClick={open(pool)}
            >
              SNAT {pool}
            </button>
          ))
        ) : (
          <span
            className="chip info"
            title={L("Source-NAT auf die IP des ausgehenden Interfaces", "Source NAT to the IP of the outgoing interface")}
          >
            {L("SNAT Interface-IP", "SNAT interface IP")}
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
        <span className="chip local" title={L("Cisco-ACI-Contract", "Cisco ACI contract")}>
          ACI
        </span>{" "}
        {r.aci.kind === "taboo" && (
          <>
            <span
              className="chip crit"
              title={L(
                "Taboo-Contract: verbietet den Verkehr zu den geschützten EPGs",
                "Taboo contract: denies traffic to the protected EPGs",
              )}
            >
              Taboo
            </span>{" "}
          </>
        )}
        {r.aci.kind === "preferred_group" && (
          <>
            <span
              className="chip info"
              title={L(
                "Preferred Group: Mitglieder der VRF sprechen frei miteinander",
                "Preferred Group: members of the VRF communicate freely with each other",
              )}
            >
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
        <span
          key={n}
          className="chip ok"
          title={L(
            `gleiche Policy auf ${n} (Verbund ${p.group}) – zusammengefasst`,
            `same policy on ${n} (parallel group ${p.group}) – merged`,
          )}
        >
          + {n}
        </span>
      ))}
      {(p.differs ?? []).map((n) => (
        <span
          key={n}
          className="chip warn"
          title={L(`auf ${n} anders konfiguriert – siehe Findings`, `configured differently on ${n} – see findings`)}
        >
          ≠ {n}
        </span>
      ))}
      {(p.missing ?? []).map((n) => (
        <span
          key={n}
          className="chip warn"
          title={L(`fehlt auf ${n} (Verbund ${p.group})`, `missing on ${n} (parallel group ${p.group})`)}
        >
          {L(`fehlt auf ${n}`, `missing on ${n}`)}
        </span>
      ))}
      {p.lead && (
        <span
          className="chip warn"
          title={L(
            `weicht von ${p.lead} ab: ${(p.fields ?? []).join(", ")}`,
            `differs from ${p.lead}: ${(p.fields ?? []).join(", ")}`,
          )}
        >
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
      <ErrorBox error={error} prefix={L("Verbund", "Parallel group")} />
      {open.map((s) => (
        <Note key={s.system_ids.join("-")}>
          {L(
            <>
              <b>{s.names.join(" und ")}</b> sehen parallel aus ({s.reasons.join(", ")}) – ihre Regeln stehen doppelt
              in der Liste.
            </>,
            <>
              <b>{s.names.join(" and ")}</b> look parallel ({s.reasons.join(", ")}) – their rules appear twice in the
              list.
            </>,
          )}{" "}
          {isAdmin ? (
            <>
              <button
                className="btn btn-sm btn-primary"
                disabled={busy}
                onClick={() => act(setParallelGroup(tenantId, s.group, s.system_ids), true)}
              >
                {L(<>Als Verbund „{s.group}“ zusammenfassen</>, <>Merge as parallel group “{s.group}”</>)}
              </button>{" "}
              <button
                className="btn btn-sm"
                disabled={busy}
                title={L("nicht parallel – wird nicht mehr vorgeschlagen", "not parallel – no longer suggested")}
                onClick={() => act(rejectParallel(tenantId, { system_ids: s.system_ids }), false)}
              >
                {L("Ablehnen", "Reject")}
              </button>
            </>
          ) : (
            <span className="faint">
              {L(
                "Ein Admin kann sie als Verbund zusammenfassen oder ablehnen.",
                "An admin can merge them as a parallel group or reject them.",
              )}
            </span>
          )}
        </Note>
      ))}
      {isAdmin && rejected.length > 0 && (
        <div className="faint" style={{ margin: "0 0 10px" }}>
          <button className="linkbtn" onClick={() => setShowRejected((v) => !v)}>
            {L(
              <>
                {rejected.length} abgelehnte{rejected.length === 1 ? "r" : ""} Vorschlag{rejected.length === 1 ? "" : "e"}
              </>,
              <>
                {rejected.length} rejected suggestion{rejected.length === 1 ? "" : "s"}
              </>,
            )}
          </button>
          {showRejected &&
            rejected.map((s) => (
              <div key={s.system_ids.join("-")} style={{ marginTop: 4 }}>
                {L(<>{s.names.join(" und ")} – nicht parallel</>, <>{s.names.join(" and ")} – not parallel</>)}{" "}
                <button
                  className="linkbtn"
                  disabled={busy}
                  onClick={() => act(unrejectParallel(tenantId, s.system_ids), false)}
                >
                  {L("wieder vorschlagen", "suggest again")}
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
  canAssign = false,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // object= / nat= / q= aus einem Link (Objekt, NAT, Finding)
  onGo?: (v: View, p?: ViewParams) => void;
  isAdmin?: boolean;
  customer?: boolean; // Kunden-Benutzer: keine Verbund-Vorschläge (ADR 0018)
  canAssign?: boolean; // Operator: Regel einer Applikation zuordnen (0.60.0)
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
  // Tönung je IPv4/IPv6-Paar (0.38.0): aufeinanderfolgende Paare abwechselnd
  const pairTone = new Map<number, string>();
  filtered.forEach((r, i) => {
    const next = filtered[i + 1];
    if (r.pair && next?.id === r.pair.rule_id && next.pair?.rule_id === r.id) {
      const tone = pairTone.size % 4 === 0 ? "pair-a" : "pair-b";
      pairTone.set(r.id, tone);
      pairTone.set(next.id, tone);
    }
  });
  const loaded = rules?.length ?? 0;
  // ACI: Quelle = Consumer-EPGs, Ziel = Provider-EPGs
  const srcHead =
    domain === "aci_contract"
      ? "Consumer"
      : domain === "firewall"
        ? L("Quelle", "Source")
        : L("Quelle / Consumer", "Source / consumer");
  const dstHead =
    domain === "aci_contract"
      ? "Provider"
      : domain === "firewall"
        ? L("Ziel", "Destination")
        : L("Ziel / Provider", "Destination / provider");

  return (
    <>
      <PageHead title={L("Regeln & Contracts", "Rules & contracts")}>
        {rules === null
          ? L("Lade…", "Loading…")
          : L(
              `${total} Einträge für „${tenantLabel}“ – Firewall-Policies und ACI-Contracts, read-only eingelesen und gegen die Source of Truth aufgelöst.`,
              `${total} entries for “${tenantLabel}” – firewall policies and ACI contracts, collected read-only and resolved against the source of truth.`,
            )}
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
            placeholder={L("Suche: Objekt, IP, DNS-Name, NAT, Zone…", "Search: object, IP, DNS name, NAT, zone…")}
            title={L(
              "Sucht in allem: Regel, Objekte und Gruppenmitglieder, IP (auch im Netz), Netz/CIDR (Regeln, die sich mit dem Netz überschneiden), DNS-Name aus dem IPAM, Service, Zone, EPG, NAT, Kommentar. Mehrere Wörter = alle müssen vorkommen.",
              "Searches everything: rule, objects and group members, IP (also within a network), network/CIDR (rules that overlap the network), DNS name from the IPAM, service, zone, EPG, NAT, comment. Several words = all must occur.",
            )}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        {net && (
          <span
            className="chip local"
            title={L(
              "Regeln, deren Quelle oder Ziel sich mit dem Subnetz überschneidet (auch über Gruppen)",
              "Rules whose source or destination overlaps the subnet (also via groups)",
            )}
          >
            {L("Subnetz", "Subnet")} {net}
            <button className="linkbtn" title={L("Filter entfernen", "Remove filter")} onClick={() => onGo?.("regeln")}>
              ✕
            </button>
          </span>
        )}
        {net && (
          <button
            className={`toggle ${netAny ? "on" : ""}`}
            title={L("Regeln mit any als Quelle bzw. Ziel einbeziehen", "Include rules with any as source or destination")}
            onClick={() => setNetAny((v) => !v)}
          >
            <span className="dot" />
            {L("inkl. any", "incl. any")}
          </button>
        )}
        {(object || nat) && (
          <span
            className="chip local"
            title={L(
              "aus einem Link – Regeln, die dieses Objekt bzw. diesen NAT-Eintrag verwenden",
              "from a link – rules that use this object or this NAT entry",
            )}
          >
            {object
              ? `${L("Objekt", "Object")} ${object}${
                  params.family ? ` (${FAMILY_LABEL[params.family] ?? params.family})` : ""
                }`
              : `NAT ${nat}`}
            <button className="linkbtn" title={L("Filter entfernen", "Remove filter")} onClick={() => onGo?.("regeln")}>
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
          <option value="">{L("Art: alle", "Type: all")}</option>
          <option value="firewall">{L("Firewall-Regeln", "Firewall rules")}</option>
          <option value="aci_contract">{L("ACI-Contracts", "ACI contracts")}</option>
        </select>
        <select className="sel" value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">{L("System: alle", "System: all")}</option>
          {ruleSystems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select className="sel" value={srcZone} onChange={(e) => setSrcZone(e.target.value)}>
          <option value="">{L("Quell-Zone: alle", "Source zone: all")}</option>
          {srcZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={dstZone} onChange={(e) => setDstZone(e.target.value)}>
          <option value="">{L("Ziel-Zone: alle", "Destination zone: all")}</option>
          {dstZones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <select className="sel" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">{L("Aktion: alle", "Action: all")}</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          className="sel"
          value={family}
          onChange={(e) => setFamily(e.target.value)}
          title={L("Adressfamilie der Policy", "Address family of the policy")}
        >
          <option value="">{L("Familie: alle", "Family: all")}</option>
          <option value="v4">IPv4</option>
          <option value="v6">IPv6</option>
          <option value="dual">IPv4+IPv6</option>
        </select>
        {multiVdom && (
          <select
            className="sel"
            value={vdom}
            onChange={(e) => setVdom(e.target.value)}
            title={L("FortiGate-VDOM der Policy", "FortiGate VDOM of the policy")}
          >
            <option value="">{L("VDOM: alle", "VDOM: all")}</option>
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
          title={L(
            "IPv4/IPv6-Paare: „ähnlich“ = vorgeschlagen, noch nicht bestätigt",
            "IPv4/IPv6 pairs: “similar” = suggested, not yet confirmed",
          )}
        >
          <option value="">{L("Paar: alle", "Pair: all")}</option>
          <option value="similar">{L("ähnlich (offen)", "similar (open)")}</option>
          <option value="confirmed">{L("bestätigt", "confirmed")}</option>
          <option value="same">{L("gleich", "identical")}</option>
          <option value="any">{L("mit Paar", "with pair")}</option>
          <option value="none">{L("ohne Paar", "without pair")}</option>
        </select>
        {hasGroups && (
          <select
            className="sel"
            value={parallel}
            onChange={(e) => setParallel(e.target.value)}
            title={L(
              "Parallele Firewalls (Verbund): gleiche Policies sind zusammengefasst",
              "Parallel firewalls (parallel group): identical policies are merged",
            )}
          >
            <option value="">{L("Verbund: alle", "Parallel group: all")}</option>
            <option value="differs">{L("abweichend", "differing")}</option>
            <option value="missing">{L("fehlt auf einer Firewall", "missing on one firewall")}</option>
          </select>
        )}
        <button className={`toggle ${onlyFindings ? "on" : ""}`} onClick={() => setOnlyFindings((v) => !v)}>
          <span className="dot y" />
          {L("nur mit Findings", "only with findings")}
        </button>
        {impossible !== null && (impossible > 0 || routable) && (
          <button
            className={`toggle ${routable ? "on" : ""}`}
            onClick={() => setRoutable((v) => !v)}
            title={L(
              "Regeln, die die gesuchte IP nach dem Routing ihrer Firewall nicht sehen können (Ein- bzw. Ausgang passt nicht)",
              "Rules that cannot see the searched IP according to the routing of their firewall (incoming or outgoing interface does not match)",
            )}
          >
            <span className="dot" />
            {L(
              <>{impossible} routingtechnisch unmögliche ausblenden</>,
              <>Hide {impossible} impossible by routing</>,
            )}
          </button>
        )}
        <span className="count">
          {L(
            <>
              {filtered.length} Treffer{total > loaded ? ` · ${loaded} von ${total} geladen` : ""}
            </>,
            <>
              {filtered.length} match{filtered.length === 1 ? "" : "es"}
              {total > loaded ? ` · ${loaded} of ${total} loaded` : ""}
            </>,
          )}
        </span>
      </div>

      <ErrorBox error={error} prefix={L("Regeln laden", "Load rules")} />

      <div className="card">
        <div className="card-body scroll viewport">
          {rules === null ? (
            <Loading>{L("Lade Regeln…", "Loading rules…")}</Loading>
          ) : filtered.length === 0 ? (
            <Empty title={rules.length ? L("Keine Treffer", "No matches") : L("Noch keine Regeln", "No rules yet")}>
              {rules.length
                ? L("Filter oder Suche anpassen.", "Adjust the filter or search.")
                : L(
                    "Sobald ein Quellsystem eingelesen wurde, erscheinen hier die Regeln und Contracts.",
                    "Once a source system has been collected, its rules and contracts appear here.",
                  )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>{L("Aktion", "Action")}</th>
                  <th>System</th>
                  <th>{L("Quell-Zone", "Source zone")}</th>
                  <th>{L("Ziel-Zone", "Destination zone")}</th>
                  <th>{srcHead}</th>
                  <th>{dstHead}</th>
                  <th>{L("Dienste", "Services")}</th>
                  <th title={L("nicht aufgelöste Objekte", "unresolved objects")}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  // IPv4/IPv6-Paar (0.36.0): das Gegenstück steht direkt darunter; seit 0.38.0
                  // beide in einem Rahmen, je Paar abwechselnd getönt (Vorschläge = ähnlich, gelb)
                  const next = filtered[i + 1];
                  const prev = filtered[i - 1];
                  const head = !!r.pair && next?.id === r.pair.rule_id && next.pair?.rule_id === r.id;
                  const tail = !!r.pair && prev?.id === r.pair.rule_id && prev.pair?.rule_id === r.id;
                  const tone = ` ${pairTone.get(r.id) ?? ""}`;
                  const kind = r.pair?.match === "similar" ? " pair-similar" : "";
                  const pairCls = head ? ` pair-head${kind}${tone}` : tail ? ` pair-tail${kind}${tone}` : "";
                  return (
                  <tr
                    key={r.id}
                    className={`clickable${pairHover === r.id ? " pair-hl" : ""}${pairCls}`}
                    onClick={() => setSelected(r.id)}
                    onMouseEnter={() => setPairHover(r.pair?.rule_id ?? null)}
                    onMouseLeave={() => setPairHover(null)}
                    title={
                      head || tail
                        ? r.pair?.match === "similar"
                          ? L("Paar-Vorschlag IPv4/IPv6 (ähnlich)", "Suggested IPv4/IPv6 pair (similar)")
                          : L("IPv4/IPv6-Paar", "IPv4/IPv6 pair")
                        : undefined
                    }
                  >
                    <td className="mono">
                      {tail ? (
                        <span className="faint" title={L("Gegenstück der Regel darüber", "Counterpart of the rule above")}>
                          ↳{" "}
                        </span>
                      ) : null}
                      {r.id}
                      {head && (
                        <div>
                          <span
                            className="pair-tag"
                            title={
                              r.pair?.match === "similar"
                                ? L("Paar-Vorschlag (ähnlich)", "Suggested pair (similar)")
                                : L("IPv4/IPv6-Paar", "IPv4/IPv6 pair")
                            }
                          >
                            {r.pair?.match === "similar" ? L("Paar?", "Pair?") : L("Paar", "Pair")}
                          </span>
                        </div>
                      )}
                    </td>
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
                            {L(<>für {q.trim()} nicht möglich</>, <>not possible for {q.trim()}</>)}
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
                      <ObjCell items={serviceNames(r)} />
                    </td>
                    <td>
                      {r.num_unmatched > 0 ? (
                        <span className="chip warn" title={L("nicht aufgelöste Objekte", "unresolved objects")}>
                          {r.num_unmatched}
                        </span>
                      ) : (
                        <span className="chip ok">✓</span>
                      )}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {rules !== null && total > loaded && (
          <div className="card-foot">
            {L(
              <>{loaded} von {total} geladen – Suche, Art oder System eingrenzen oder</>,
              <>{loaded} of {total} loaded – narrow down search, type or system, or</>,
            )}{" "}
            <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
              {more
                ? L("Lade…", "Loading…")
                : L(`weitere ${Math.min(PAGE, total - loaded)} laden`, `load ${Math.min(PAGE, total - loaded)} more`)}
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
          intro={canAssign && tenantId != null ? <AssignToApp tenantId={tenantId} ruleId={selected} onGo={onGo} /> : null}
        />
      )}
    </>
  );
}

// Regel einer Applikation direkt zuordnen (0.60.0) – auch ohne Treffer bei ihren Systemen
function AssignToApp({ tenantId, ruleId, onGo }: { tenantId: number; ruleId: number; onGo?: (v: View, p?: ViewParams) => void }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [app, setApp] = useState("");
  const [done, setDone] = useState<Application | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setDone(null);
    fetchApps(tenantId)
      .then(setApps)
      .catch(() => setApps([]));
  }, [tenantId, ruleId]);
  if (!apps?.length) return null;
  const assign = () => {
    const target = apps.find((a) => String(a.id) === app);
    if (!target) return;
    assignAppRule(target.id, ruleId)
      .then(() => setDone(target))
      .catch(setError);
  };
  return (
    <div id="rule-assign-app" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
      <span className="faint">{L("Applikation:", "Application:")}</span>
      <select
        className="sel"
        aria-label={L("Applikation", "Application")}
        value={app}
        onChange={(e) => setApp(e.target.value)}
      >
        <option value="">{L("wählen…", "choose…")}</option>
        {apps.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
      <button className="btn btn-ghost btn-sm" disabled={!app} onClick={assign}>
        {L("Zuordnen", "Assign")}
      </button>
      {done && (
        <button className="linkbtn objlink" onClick={() => onGo?.("apps", { app: String(done.id) })}>
          {L(<>zugeordnet zu {done.name} – Doku öffnen</>, <>assigned to {done.name} – open documentation</>)}
        </button>
      )}
      <ErrorBox error={error} onClose={() => setError(null)} prefix={L("Zuordnen", "Assign")} />
    </div>
  );
}
