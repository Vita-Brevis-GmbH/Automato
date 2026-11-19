// NetBox-Sync, Abschnitt „Reservierungen“ (1.19.0, ADR 0066; orchestrator/app/store/netbox_sync_reserv.py): Eintrag für die
// Abschnitte der Vorschau, Arten, der Vermerk an nur angezeigten Nummern und die Bestätigung geänderter Reservierungen
// (wie Kabelnummern und DNS-Namen: `confirm`) – eigene Datei, damit NetBoxSync.tsx nur einhängt.
import { type ReactNode, useEffect, useState } from "react";
import { fetchSyncItems, type SyncItem, type SyncPlan, type SyncSection } from "../../api";
import { L } from "../../i18n";
import { ErrorBox, Loading } from "../../ui";

export const RESERVATION_SECTION: { key: SyncSection; label: string; hint: string; more?: ReactNode } = {
  key: "reservations",
  label: L("Reservierungen", "Reservations"),
  hint: L(
    "in Automato reservierte VLANs mit Status „reserved“ und AS-Nummern als ASN-Objekte",
    "VLANs reserved in Automato with status “reserved” and AS numbers as ASN objects",
  ),
  more: L(
    <>
      <b>VLANs</b> in der VLAN-Gruppe der Reservierung, sonst in der einen Gruppe am Standort (z. B. der Switches), ohne
      solche in einer eigenen Gruppe „Reservierungen &lt;Standort&gt;“; mehrere Gruppen am Standort nur als Hinweis.{" "}
      <b>AS-Nummern</b> mit der RIR, die die ASN gleicher Art (privat bzw. öffentlich) in NetBox schon tragen, sonst der
      einzigen passenden RIR – ohne eindeutige RIR nur ein Hinweis (eine RIR legt der Sync nie an). Was NetBox schon hat (VLAN
      mit dieser Nummer in der Gruppe, ASN), wird nur angezeigt und nie überschrieben; liest ein Gerät das VLAN, gewinnt
      das Gelesene. Freigegebene Reservierungen sind veraltet bzw. werden mit „Löschen zulassen“ entfernt – andere
      Abschnitte lassen reservierte VLANs stehen, und Ports nehmen nie ein reserviertes VLAN.
    </>,
    <>
      <b>VLANs</b> in the VLAN group of the reservation, otherwise in the one group at the site (e.g. of the switches),
      without such in an own group “Reservations &lt;site&gt;”; several groups at the site only as a hint.{" "}
      <b>AS numbers</b> with the RIR that the ASNs of the same kind (private or public) already have in NetBox, otherwise
      the only matching RIR – without a unique RIR only a hint (the sync never creates an RIR). What NetBox already has
      (VLAN with this number in the group, ASN) is only shown and never overwritten; if a device reads the VLAN, what is
      read wins. Released reservations are obsolete or removed with “Allow deletion” – other sections leave reserved
      VLANs alone, and ports never take a reserved VLAN.
    </>,
  ),
};

export const RESERVATION_KINDS: Record<string, string> = { rir: "RIR", asn: "ASN" };

// Nur angezeigt (Konflikt ohne Schreiben): NetBox hat die Nummer schon
export function ReservationNote({ item }: { item: SyncItem }) {
  if (item.note !== "exists") return null;
  return (
    <div className="sync-reserved-note">
      <span className="chip na">{L("Reservierung – nur angezeigt", "Reservation – only shown")}</span>{" "}
      {item.reservation && <span className="faint">{item.reservation}</span>}
    </div>
  );
}

const RESERVED_KINDS = new Set(["vlan", "vlan_group", "asn"]);

// Bestehende reservierte Objekte (VLAN, Gruppe, ASN) ändert der Sync nur bestätigt: beide Werte, Kästchen je Eintrag.
export function ReservationConfirm({
  plan,
  value,
  onChange,
  onAsked,
}: {
  plan: SyncPlan;
  value: string[];
  onChange: (keys: string[]) => void;
  onAsked: (n: number) => void;
}) {
  const [rows, setRows] = useState<SyncItem[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    fetchSyncItems(plan.id, { action: "update", limit: 2000, offset: 0 })
      .then((d) => {
        if (!live) return;
        const asked = d.rows.filter((r) => r.confirm && RESERVED_KINDS.has(r.kind));
        setRows(asked);
        onAsked(asked.length);
      })
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [plan.id, onAsked]);
  if (err) return <ErrorBox error={err} />;
  if (rows === null) return <Loading />;
  if (!rows.length) return null;
  const chosen = new Set(value);
  const toggle = (key: string, on: boolean) => onChange(on ? [...value, key] : value.filter((k) => k !== key));
  return (
    <div className="card" id="sync-reservations">
      <div className="card-head">
        <h2>{L("Geänderte Reservierungen bestätigen", "Confirm changed reservations")}</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {L(`${value.length} von ${rows.length} bestätigt`, `${value.length} of ${rows.length} confirmed`)}
        </span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" id="sync-reservations-all" onClick={() => onChange(rows.map((r) => r.key))}>
          {L("alle", "all")}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
          {L("keine", "none")}
        </button>
      </div>
      <div className="card-body pad faint" style={{ fontSize: 12 }}>
        {L(
          "Reservierte VLANs und AS-Nummern, die schon in NetBox stehen, weichen ab (neuer Zweck in Automato bzw. in NetBox von Hand geändert). Geschrieben werden nur die angekreuzten; die übrigen bleiben in NetBox, wie sie sind.",
          "Reserved VLANs and AS numbers already in NetBox differ (new purpose in Automato or changed by hand in NetBox). Only the ticked ones are written; the others stay in NetBox as they are.",
        )}
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th />
              <th>{L("Objekt", "Object")}</th>
              <th>{L("Feld", "Field")}</th>
              <th>NetBox</th>
              <th>Automato</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) =>
              (r.changes ?? []).map((c, i) => (
                <tr key={`${r.key}-${c.field}`} data-key={r.key}>
                  <td>
                    {i === 0 && (
                      <input
                        type="checkbox"
                        aria-label={L("bestätigen", "confirm")}
                        checked={chosen.has(r.key)}
                        onChange={(e) => toggle(r.key, e.target.checked)}
                      />
                    )}
                  </td>
                  <td className="mono">{i === 0 ? r.label : ""}</td>
                  <td className="mono">{c.field}</td>
                  <td className="mono">{c.old != null && c.old !== "" ? String(c.old) : <span className="faint">{L("(leer)", "(empty)")}</span>}</td>
                  <td className="mono">
                    <b>{String(c.new ?? "")}</b>
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
