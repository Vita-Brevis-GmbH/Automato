// NetBox-Sync, Abschnitt „Reservierungen“ (1.19.0, ADR 0066; orchestrator/app/store/netbox_sync_reserv.py): Eintrag für die
// Abschnitte der Vorschau, Arten und der Vermerk an nur angezeigten Nummern – eigene Datei, damit NetBoxSync.tsx nur einhängt.
import type { ReactNode } from "react";
import type { SyncItem, SyncSection } from "../../api";
import { L } from "../../i18n";

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
