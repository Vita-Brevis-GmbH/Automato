// VLANs (1.19.0, ADR 0067: eigene Ansicht in L2, vorher Reiter der Verkabelung): je Standort die VLANs der Switches bzw. des
// ACI (1.9.0). Rein lesend.
import { VlansTab } from "../../Cabling";
import { L } from "../../i18n";
import { Note, PageHead } from "../../ui";

export default function Vlans({ tenantId, tenantLabel }: { tenantId: number | null; tenantLabel: string }) {
  return (
    <>
      <PageHead title="VLANs">
        {L(
          <>
            VLANs von „{tenantLabel}“ je Standort – gelesen von den Switches bzw. aus dem ACI; Standort des Quellsystems. Ob ein
            VLAN auf den Trunks eines Wegs erlaubt ist, prüft <a href="#vergleich">Wege vergleichen</a>, reservieren unter{" "}
            <a href="#l2/vlan-reservierungen">VLAN-Reservierungen</a>. Rein lesend.
          </>,
          <>
            VLANs of “{tenantLabel}” per site – read from the switches or from the ACI; site of the source system. Whether a VLAN
            is allowed on the trunks of a path is checked by <a href="#vergleich">compare paths</a>, reserve under{" "}
            <a href="#l2/vlan-reservierungen">VLAN reservations</a>. Read-only.
          </>,
        )}
      </PageHead>
      {tenantId === null ? (
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      ) : (
        <VlansTab tenantId={tenantId} />
      )}
    </>
  );
}
