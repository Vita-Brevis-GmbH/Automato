import { useEffect, useMemo, useState } from "react";
import { fetchRules, fetchRuleSummary, fetchSystems, Rule, RuleDomain, RuleSummary, System } from "./api";
import type { View, ViewParams } from "./App";
import RuleDetailDrawer from "./RuleDetail";
import { ActionBadge, Dash, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

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

// ACI-Contract: Contract + Subject, darunter ACI-Tenant und Scope
function NameCell({ r, onGo }: { r: Rule; onGo?: (v: View, p?: ViewParams) => void }) {
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
    </>
  );
}

export default function RulesTable({
  tenantId,
  tenantLabel,
  params = {},
  onGo,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // object= / nat= / q= aus einem Link (Objekt, NAT, Finding)
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [systems, setSystems] = useState<System[]>([]);
  const [summary, setSummary] = useState<RuleSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const object = params.object ?? "";
  const nat = params.nat ?? "";

  const [q, setQ] = useState(params.q ?? "");
  const [domain, setDomain] = useState<RuleDomain | "">("");
  const [system, setSystem] = useState("");
  const [srcZone, setSrcZone] = useState("");
  const [dstZone, setDstZone] = useState("");
  const [action, setAction] = useState("");
  const [onlyFindings, setOnlyFindings] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    setSystem("");
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
      nat,
      srcZone,
      dstZone,
      action,
      limit: PAGE,
    }),
    [q, tenantId, domain, system, object, nat, srcZone, dstZone, action],
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
  const ruleSystems = systems.filter((s) =>
    domain ? s.platform === RULE_PLATFORMS[domain] : Object.values(RULE_PLATFORMS).includes(s.platform),
  );

  // Auswahl der Zonen/Aktionen aus den Kennzahlen aller Regeln (nicht nur der geladenen Seite)
  const zoneNames = (rows?: [string, number][]) => (rows ?? []).map(([z]) => z).filter((z) => z && z !== "–").sort();
  const srcZones = zoneNames(summary?.by_src_zone);
  const dstZones = zoneNames(summary?.by_dst_zone);
  const actions = Object.keys(summary?.by_action ?? {}).sort();

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

      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Suche: Objekt, IP, DNS-Name, NAT, Zone…"
            title="Sucht in allem: Regel, Objekte und Gruppenmitglieder, IP (auch im Netz), DNS-Name aus dem IPAM, Service, Zone, EPG, NAT, Kommentar. Mehrere Wörter = alle müssen vorkommen."
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        {(object || nat) && (
          <span className="chip local" title="aus einem Link – Regeln, die dieses Objekt bzw. diesen NAT-Eintrag verwenden">
            {object ? `Objekt ${object}` : `NAT ${nat}`}
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
        <button className={`toggle ${onlyFindings ? "on" : ""}`} onClick={() => setOnlyFindings((v) => !v)}>
          <span className="dot y" />
          nur mit Findings
        </button>
        <span className="count">
          {filtered.length} Treffer{total > loaded ? ` · ${loaded} von ${total} geladen` : ""}
        </span>
      </div>

      <ErrorBox error={error} prefix="Regeln laden" />

      <div className="card">
        <div className="card-body scroll">
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
                  <tr key={r.id} className="clickable" onClick={() => setSelected(r.id)}>
                    <td className="mono">{r.id}</td>
                    <td>
                      <NameCell r={r} onGo={onGo} />
                    </td>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td>{systemName(r.source_system_id)}</td>
                    <td>
                      <ZoneChip zone={r.src_zone} />
                    </td>
                    <td>
                      <ZoneChip zone={r.dst_zone} />
                    </td>
                    <td>
                      <ObjCell items={r.sources} />
                    </td>
                    <td>
                      <ObjCell items={r.destinations} />
                    </td>
                    <td>
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
        <RuleDetailDrawer ruleId={selected} systemName={systemName} onClose={() => setSelected(null)} onGo={onGo} />
      )}
    </>
  );
}
