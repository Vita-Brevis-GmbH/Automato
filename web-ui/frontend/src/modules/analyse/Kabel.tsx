// Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Kabelnummer mit Herkunft (NetBox bzw.
// Automato), Dialog zum Erfassen je Port-Paar, Transceiver je Port und der Reiter „Kabelnummern“.
// Erfasst wird nur in Automato (Operator); nach NetBox schreibt allein der NetBox-Sync (Vorschau →
// Übernehmen, je Kabel bestätigt). Die Geräte liest Automato nur.
import { FormEvent, useEffect, useRef, useState } from "react";
import { L } from "../../i18n";
import { Dash, Empty, ErrorBox, Loading, Modal, Note } from "../../ui";
import {
  CableLabelInfo,
  CableLabelRow,
  CableLabelSummary,
  ChoiceOrigin,
  deleteCableLabel,
  DeviceChoice,
  fetchCableLabels,
  fetchLabelDevices,
  fetchLabelPorts,
  PeerChoice,
  PortChoice,
  PortRef,
  saveCableLabel,
  Transceiver,
  TransceiverStatus,
} from "./kabelApi";

const ORIGIN: Record<string, [string, string, string]> = {
  same: ["ok", L("Automato = NetBox", "Automato = NetBox"), L("in Automato erfasst und in NetBox gleich", "captured in Automato and the same in NetBox")],
  differs: ["warn", L("verschieden", "differs"), L("in Automato und NetBox verschieden – der NetBox-Sync zeigt beide Werte", "differs between Automato and NetBox – the NetBox sync shows both values")],
  automato: ["info", "Automato", L("nur in Automato erfasst – der NetBox-Sync schreibt sie als Label des Kabels", "captured in Automato only – the NetBox sync writes it as the label of the cable")],
  netbox: ["na", "NetBox", L("nur in NetBox – wird angezeigt, nie gelöscht", "only in NetBox – shown, never deleted")],
};

// Kabelnummer einer Verbindung bzw. eines Ports mit Herkunft; Stift zum Erfassen (Operator)
export function LabelCell({ info, onEdit }: { info: CableLabelInfo | null | undefined; onEdit?: () => void }) {
  const mine = info?.automato?.label ?? null;
  const theirs = [...new Set((info?.netbox ?? []).map((c) => c.label).filter((x): x is string => !!x))];
  const state = info?.state ?? null;
  const edit = onEdit && (
    <button
      className="chip cable-label-edit"
      style={{ cursor: "pointer", fontFamily: "inherit" }}
      onClick={onEdit}
      title={mine ? L("Kabelnummer ändern", "Change cable number") : L("Kabelnummer erfassen", "Capture cable number")}
    >
      {mine ? "✎" : L("+ Nummer", "+ number")}
    </button>
  );
  if (!state) return <span className="cable-label">{edit ?? <Dash />}</span>;
  const [cls, label, title] = ORIGIN[state];
  return (
    <span className="cable-label" data-state={state}>
      {state === "differs" ? (
        <>
          <b className="mono">{mine}</b> <span className="faint">(Automato)</span> ·{" "}
          <b className="mono">{theirs.join(", ")}</b> <span className="faint">(NetBox)</span>
        </>
      ) : (
        <b className="mono">{mine ?? theirs.join(", ")}</b>
      )}{" "}
      <span className={`chip ${cls}`} title={title}>
        {label}
      </span>{" "}
      {edit}
    </span>
  );
}

export function transceiverText(t: Transceiver | null | undefined): string {
  if (!t) return "";
  const parts = [t.type, t.part && t.part !== t.type ? t.part : null, t.vendor, t.serial ? `S/N ${t.serial}` : null];
  return parts.filter(Boolean).join(" · ");
}

export function TransceiverCell({ t }: { t: Transceiver | null | undefined }) {
  if (!t) return <span className="faint">–</span>;
  return (
    <span className="transceiver" title={L("Transceiver am Port (gelesen)", "Transceiver on the port (read)")}>
      {transceiverText(t)}
    </span>
  );
}

const READ: Record<TransceiverStatus, [string, string, string]> = {
  read: ["ok", L("gelesen", "read"), L("Transceiver gelesen", "transceivers read")],
  unreadable: ["warn", L("nicht lesbar", "not readable"), L("Befehl gescheitert – Grund in der Meldung des Laufs", "command failed – reason in the message of the run")],
  unsupported: ["na", L("nicht unterstützt", "not supported"), L("über diesen Zugang nicht lesbar (z. B. SNMPv3)", "not readable via this access (e.g. SNMPv3)")],
};

export function TransceiverStatusChip({ status, count }: { status: TransceiverStatus | null | undefined; count?: number }) {
  if (!status) return <span className="faint" title={L("nicht geliefert (älterer Agent bzw. kein Switch)", "not delivered (older agent or not a switch)")}>–</span>;
  const [cls, label, title] = READ[status];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
      {status === "read" && count !== undefined ? ` · ${count}` : ""}
    </span>
  );
}

// Herkunft eines Geräts in der Auswahl (1.23.0): Chip-Klasse, Text, Erklärung
const DEVICE_ORIGIN: Record<ChoiceOrigin, [string, string, string]> = {
  inventory: ["ok", L("gelesen", "read"), L("von Automato gelesen – nur seine gelesenen Ports sind wählbar", "read by Automato – only its read ports can be chosen")],
  neighbor: ["info", "CDP/LLDP", L("nur von Nachbarn gemeldet – gemeldete Ports wählbar, weitere frei", "only reported by neighbors – reported ports can be chosen, others free")],
  netbox: ["na", "NetBox", L("nur am Kabel in NetBox – Port frei", "only at a cable in NetBox – port free")],
  free: ["warn", L("frei erfasst", "entered freely"), L("früher frei erfasst (kein gelesenes Gerät, z. B. Patchpanel)", "entered freely before (no read device, e.g. patch panel)")],
};
const VIA: Record<string, string> = { cdp: "CDP", lldp: "LLDP", netbox: "NetBox", automato: "Automato" };

export function FreeChip({ title }: { title?: string }) {
  return (
    <span
      className="chip warn cl-free"
      title={title ?? L("kein gelesenes Gerät – frei erfasst (z. B. Patchpanel)", "no read device – entered freely (e.g. patch panel)")}
    >
      {L("frei", "free")}
    </span>
  );
}

const sameEnd = (x: Partial<PortRef>, y: Partial<PortRef>) =>
  !!x.device && !!y.device && x.device.toLowerCase() === y.device.toLowerCase() && (x.port ?? "").toLowerCase() === (y.port ?? "").toLowerCase();

// Ein Ende: Gerät mit Suche (gelesene zuerst), dann die Ports dieses Geräts; frei nur ohne gelesenes Gerät
function EndPicker({
  end,
  title,
  tenantId,
  value,
  locked,
  onChange,
  onPorts,
}: {
  end: "a" | "b";
  title: string;
  tenantId: number;
  value: { device: string; port: string };
  locked: boolean;
  onChange: (v: { device: string; port: string }) => void;
  onPorts: (ports: PortChoice[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<{ total: number; items: DeviceChoice[] } | null>(null);
  const [loaded, setLoaded] = useState<{ name: string; device: DeviceChoice | null; ports: PortChoice[] } | null>(null);
  const blur = useRef<number | undefined>(undefined);
  // Treffer der Gerätesuche
  useEffect(() => {
    if (locked || !open) return;
    let live = true;
    const t = setTimeout(() => {
      fetchLabelDevices(tenantId, value.device.trim())
        .then((d) => live && setFound(d))
        .catch(() => live && setFound(null));
    }, 150);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [tenantId, value.device, open, locked]);
  // Ports des Geräts (auch bei festem Ende: für den Vorschlag der Gegenstelle)
  useEffect(() => {
    const name = value.device.trim();
    if (!name) {
      setLoaded(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      fetchLabelPorts(tenantId, name)
        .then((d) => live && setLoaded({ name, ...d }))
        .catch(() => live && setLoaded(null));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [tenantId, value.device]);
  const info = loaded && loaded.name === value.device.trim() ? loaded : null; // nur zum eingegebenen Namen
  const ports = info?.device ? info.ports : null;
  useEffect(() => onPorts(ports), [ports]); // eslint-disable-line react-hooks/exhaustive-deps
  const dev = info?.device ?? null;
  const known = ports?.some((p) => p.name.toLowerCase() === value.port.trim().toLowerCase()) ?? false;
  const listOnly = !!dev && !dev.free_ports; // gelesenes Gerät: nur seine Ports
  const unknownDevice = !!value.device.trim() && info !== null && !dev;
  const port = ports?.find((p) => p.name.toLowerCase() === value.port.trim().toLowerCase());
  return (
    <fieldset className="cl-end" data-end={end} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "6px 10px 8px", margin: "0 0 10px" }}>
      <legend style={{ fontSize: 12, fontWeight: 600, padding: "0 4px" }}>{title}</legend>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div className="field" style={{ flex: "1 1 200px", position: "relative", marginBottom: 0 }}>
          <label htmlFor={`cl-${end}-device`}>
            {L("Gerät", "Device")}{" "}
            {dev && (
              <span className={`chip ${DEVICE_ORIGIN[dev.origin][0]} cl-origin`} data-origin={dev.origin} title={DEVICE_ORIGIN[dev.origin][2]}>
                {DEVICE_ORIGIN[dev.origin][1]}
              </span>
            )}
            {unknownDevice && <FreeChip />}
          </label>
          <input
            id={`cl-${end}-device`}
            className="input mono"
            value={value.device}
            readOnly={locked}
            maxLength={200}
            autoComplete="off"
            placeholder={locked ? undefined : L("Gerät suchen …", "Search device …")}
            role={locked ? undefined : "combobox"}
            aria-expanded={locked ? undefined : open}
            onChange={(e) => {
              onChange({ device: e.target.value, port: "" });
              setOpen(true);
            }}
            onFocus={() => {
              if (locked) return;
              window.clearTimeout(blur.current);
              setOpen(true);
            }}
            onBlur={() => {
              blur.current = window.setTimeout(() => setOpen(false), 150);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape" && open) {
                e.stopPropagation();
                setOpen(false);
              }
            }}
          />
          {open && !locked && found && (
            <div className="menu suggest cl-devices" id={`cl-${end}-devices`} role="listbox" aria-label={L("Geräte", "Devices")} style={{ maxHeight: 260 }}>
              {found.items.map((d) => (
                <button
                  key={d.name}
                  type="button"
                  role="option"
                  aria-selected={d.name === value.device}
                  className="cl-device-option"
                  data-name={d.name}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onChange({ device: d.name, port: "" });
                    setOpen(false);
                  }}
                >
                  <b>{d.name}</b>{" "}
                  <span className={`chip ${DEVICE_ORIGIN[d.origin][0]}`} title={DEVICE_ORIGIN[d.origin][2]}>
                    {DEVICE_ORIGIN[d.origin][1]}
                  </span>{" "}
                  <span className="faint">
                    {[d.kind && d.kind !== "unknown" ? d.kind : null, d.source, L(`${d.ports ?? 0} Ports`, `${d.ports ?? 0} ports`)].filter(Boolean).join(" · ")}
                  </span>
                </button>
              ))}
              {found.items.length === 0 && (
                <div className="faint" style={{ padding: "6px 10px" }}>
                  {L("kein gelesenes Gerät – wird frei erfasst (z. B. Patchpanel)", "no read device – entered freely (e.g. patch panel)")}
                </div>
              )}
              {found.total > found.items.length && (
                <div className="faint" style={{ padding: "6px 10px" }}>
                  {L(`${found.items.length} von ${found.total} – genauer suchen`, `${found.items.length} of ${found.total} – refine the search`)}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="field" style={{ flex: "1 1 160px", marginBottom: 0 }}>
          <label htmlFor={`cl-${end}-port`}>
            Port {!locked && value.port.trim() && !known && (dev || unknownDevice) && <FreeChip title={L("Port nicht gelesen – frei erfasst", "port not read – entered freely")} />}
          </label>
          {locked ? (
            <input id={`cl-${end}-port`} className="input mono" value={value.port} readOnly maxLength={200} />
          ) : listOnly ? (
            <select id={`cl-${end}-port`} className="sel mono" value={known ? port!.name : value.port} onChange={(e) => onChange({ ...value, port: e.target.value })}>
              <option value="">{L("– Port wählen –", "– choose port –")}</option>
              {value.port && !known && (
                <option value={value.port}>
                  {value.port} {L("(nicht gelesen)", "(not read)")}
                </option>
              )}
              {ports!.map((p) => (
                <option key={p.name} value={p.name}>
                  {[p.name, p.description, p.status, p.peers.length ? `→ ${p.peers[0].device} ${p.peers[0].port}` : null].filter(Boolean).join(" · ")}
                </option>
              ))}
            </select>
          ) : (
            <>
              <input
                id={`cl-${end}-port`}
                className="input mono"
                value={value.port}
                maxLength={200}
                list={ports?.length ? `cl-${end}-ports` : undefined}
                placeholder={ports?.length ? L("wählen oder frei eingeben", "choose or enter freely") : L("frei eingeben", "enter freely")}
                onChange={(e) => onChange({ ...value, port: e.target.value })}
              />
              {!!ports?.length && (
                <datalist id={`cl-${end}-ports`}>
                  {ports.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.description ?? undefined}
                    </option>
                  ))}
                </datalist>
              )}
            </>
          )}
        </div>
      </div>
      {listOnly && !locked && ports && ports.length === 0 && (
        <div className="hint">{L("keine Ports gelesen", "no ports read")}</div>
      )}
      {port?.description && <div className="hint faint">{port.description}</div>}
    </fieldset>
  );
}

// Vorschläge der Gegenstelle eines Ports (CDP/LLDP, Kabel in NetBox, Erfassung) – übernehmen per Klick
function PeerSuggestions({ to, peers, current, onPick }: { to: string; peers: PeerChoice[]; current: Partial<PortRef>; onPick: (p: PeerChoice) => void }) {
  if (!peers.length) return null;
  return (
    <div className="hint cl-peers" style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", margin: "-4px 0 10px" }}>
      <span>{L(`Gegenstelle für ${to}:`, `Peer for ${to}:`)}</span>
      {peers.map((p) => {
        const on = sameEnd(p, current);
        return (
          <button
            key={`${p.device}|${p.port}`}
            type="button"
            className={`chip cl-peer${on ? " ok" : ""}`}
            style={{ cursor: "pointer", fontFamily: "inherit" }}
            data-device={p.device}
            data-port={p.port}
            disabled={on}
            title={L("als Gegenstelle übernehmen", "use as peer")}
            onClick={() => onPick(p)}
          >
            <b>{p.device}</b> <span className="mono">{p.port}</span> <span className="faint">({p.via.map((v) => VIA[v] ?? v).join(" · ")})</span>
            {p.labels.length > 0 && <span className="mono"> · {p.labels.join(", ")}</span>}
          </button>
        );
      })}
    </div>
  );
}

// Dialog: Kabelnummer eines Port-Paars erfassen, ändern bzw. löschen (eine Erfassung je Paar). Gerät und
// Port beider Enden aus Listen (1.23.0); fest sind Enden aus einer Verbindung bzw. einer bestehenden Erfassung.
export function CableLabelDialog({
  tenantId,
  a,
  b,
  info,
  fixed = true,
  onClose,
  onSaved,
}: {
  tenantId: number;
  a: Partial<PortRef>;
  b: Partial<PortRef>;
  info?: CableLabelInfo | null;
  // Enden nicht änderbar (nur vollständige): true = beide, "a" = nur Ende A (Port einer Zeile), false = keines
  fixed?: boolean | "a";
  onClose: () => void;
  onSaved: () => void;
}) {
  const [ends, setEnds] = useState({ a: { device: a.device ?? "", port: a.port ?? "" }, b: { device: b.device ?? "", port: b.port ?? "" } });
  const [lock] = useState(() => ({
    a: fixed !== false && !!a.device && !!a.port,
    b: fixed === true && !!b.device && !!b.port,
  }));
  const [touched, setTouched] = useState({ a: !!(a.device || a.port), b: !!(b.device || b.port) });
  const [peers, setPeers] = useState<{ a: PeerChoice[]; b: PeerChoice[] }>({ a: [], b: [] });
  const [ports, setPorts] = useState<{ a: PortChoice[] | null; b: PortChoice[] | null }>({ a: null, b: null });
  const [label, setLabel] = useState(info?.automato?.label ?? "");
  const [note, setNote] = useState(info?.automato?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const netbox = [...new Set((info?.netbox ?? []).map((c) => c.label).filter(Boolean))];
  // Vorschläge je Ende: Gegenstellen des gewählten Ports
  useEffect(() => {
    const of = (side: "a" | "b") =>
      ports[side]?.find((p) => p.name.toLowerCase() === ends[side].port.trim().toLowerCase())?.peers ?? [];
    setPeers({ a: of("a"), b: of("b") });
  }, [ports, ends]);
  // leeres, offenes Ende: bekannte Gegenstelle vorschlagen (eintragen, änderbar)
  useEffect(() => {
    for (const [from, to] of [
      ["a", "b"],
      ["b", "a"],
    ] as const) {
      const first = peers[from][0];
      if (first && !lock[to] && !touched[to] && !ends[to].device && !ends[to].port) {
        setEnds((e) => ({ ...e, [to]: { device: first.device, port: first.port } }));
        setTouched((t) => ({ ...t, [to]: true }));
      }
    }
  }, [peers]); // eslint-disable-line react-hooks/exhaustive-deps
  const known = [...peers.a.filter((p) => sameEnd(p, ends.b)), ...peers.b.filter((p) => sameEnd(p, ends.a))].flatMap((p) => p.labels);
  const existing = [...new Set(known)].filter((x) => x !== info?.automato?.label && !netbox.includes(x));
  const ok = label.trim() && ends.a.device.trim() && ends.a.port.trim() && ends.b.device.trim() && ends.b.port.trim();
  const save = async (ev?: FormEvent) => {
    ev?.preventDefault();
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      await saveCableLabel({
        tenant_id: tenantId,
        a_device: ends.a.device,
        a_port: ends.a.port,
        b_device: ends.b.device,
        b_port: ends.b.port,
        label,
        note: note || null,
      });
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!info?.automato) return;
    setBusy(true);
    try {
      await deleteCableLabel(info.automato.id, tenantId);
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const picker = (side: "a" | "b", title: string) => (
    <EndPicker
      end={side}
      title={title}
      tenantId={tenantId}
      value={ends[side]}
      locked={lock[side]}
      onChange={(v) => {
        setEnds((e) => ({ ...e, [side]: v }));
        setTouched((t) => ({ ...t, [side]: true }));
      }}
      onPorts={(p) => setPorts((x) => ({ ...x, [side]: p }))}
    />
  );
  const suggest = (from: "a" | "b", to: "a" | "b", toTitle: string) =>
    !lock[to] && (
      <PeerSuggestions
        to={toTitle}
        peers={peers[from]}
        current={ends[to]}
        onPick={(p) => {
          setEnds((e) => ({ ...e, [to]: { device: p.device, port: p.port } }));
          setTouched((t) => ({ ...t, [to]: true }));
        }}
      />
    );
  return (
    <Modal
      title={info?.automato ? L("Kabelnummer ändern", "Change cable number") : L("Kabelnummer erfassen", "Capture cable number")}
      onClose={onClose}
      footer={
        <>
          {info?.automato && (
            <button className="btn btn-ghost" id="cable-label-delete" onClick={remove} disabled={busy} style={{ marginRight: "auto" }}>
              {L("In Automato löschen", "Delete in Automato")}
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="cable-label-save" onClick={() => save()} disabled={busy || !ok}>
            {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
          </button>
        </>
      }
    >
      <form id="cable-label-dialog" onSubmit={save}>
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {picker("a", L("Ende A", "End A"))}
        {suggest("a", "b", L("Ende B", "end B"))}
        {picker("b", L("Ende B", "End B"))}
        {suggest("b", "a", L("Ende A", "end A"))}
        <div className="field">
          <label htmlFor="cl-label">{L("Kabelnummer", "Cable number")}</label>
          <input id="cl-label" className="input mono" value={label} maxLength={100} autoFocus={lock.a && lock.b} placeholder={L("z. B. K-0815", "e.g. K-0815")} onChange={(e) => setLabel(e.target.value)} />
          {netbox.length > 0 && (
            <div className="hint">
              {L("In NetBox:", "In NetBox:")} <b className="mono">{netbox.join(", ")}</b>
            </div>
          )}
          {existing.length > 0 && (
            <div className="hint cl-existing">
              {L("Für dieses Paar bekannt:", "Known for this pair:")} <b className="mono">{existing.join(", ")}</b>
            </div>
          )}
        </div>
        <div className="field">
          <label htmlFor="cl-note">{L("Notiz (optional)", "Note (optional)")}</label>
          <input id="cl-note" className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
        <Note>
          {L(
            "Gerät und Port wählen Sie aus den gelesenen Geräten; bekannte Gegenstellen (CDP/LLDP, Kabel in NetBox) werden vorgeschlagen. Frei eingeben lässt sich nur, was Automato nicht liest (z. B. ein Patchpanel) – gekennzeichnet mit „frei“. Gespeichert wird in Automato, je Port-Paar eine Nummer (Lang- und Kurzform des Ports sind dasselbe Paar). Nach NetBox schreibt sie der NetBox-Sync (Abschnitt Verkabelung) als Label des Kabels – an bestehenden Kabeln nur, wenn Sie es dort bestätigen. Was nur in NetBox steht, bleibt.",
            "Choose device and port from the read devices; known peers (CDP/LLDP, cables in NetBox) are suggested. Only what Automato does not read (e.g. a patch panel) can be entered freely – marked “free”. Stored in Automato, one number per port pair (long and short form of the port are the same pair). The NetBox sync (cabling section) writes it as the label of the cable – on existing cables only if you confirm it there. What is only in NetBox stays.",
          )}
        </Note>
      </form>
    </Modal>
  );
}

const STATE_FILTER: [string, string][] = [
  ["", L("alle", "all")],
  ["differs", L("verschieden", "differs")],
  ["automato", L("nur Automato", "Automato only")],
  ["netbox", L("nur NetBox", "NetBox only")],
  ["same", L("gleich", "same")],
  ["mismatch", L("Nachbar passt nicht", "neighbor does not match")],
];

// Reiter „Kabelnummern“: alle Port-Paare mit Nummer (Automato bzw. NetBox) und ob CDP/LLDP zum Kabel
// in NetBox passt; Erfassen auch ohne Verbindung (z. B. Port ohne CDP/LLDP)
export function CableLabelsTab({ tenantId, canEdit, initialQ }: { tenantId: number; canEdit: boolean; initialQ?: string }) {
  const [state, setState] = useState("");
  const [q, setQ] = useState(initialQ ?? "");
  const [data, setData] = useState<{ total: number; summary: CableLabelSummary; items: CableLabelRow[] } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [edit, setEdit] = useState<{ row: CableLabelRow | null } | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      fetchCableLabels(tenantId, { state, q })
        .then((d) => live && setData(d))
        .catch((e) => live && setErr(e));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [tenantId, state, q, tick]);
  const s = data?.summary;
  return (
    <div className="card" id="cabling-labels">
      <div className="card-head">
        <h2>{L("Kabelnummern", "Cable numbers")}</h2>
        {s && (
          <span className="faint" id="cabling-labels-summary" style={{ fontSize: 12 }}>
            {L(
              `${s.automato_total} in Automato · ${s.netbox_labels} von ${s.netbox_total} Kabeln in NetBox mit Nummer · ${s.differs} verschieden · ${s.mismatch} Nachbar passt nicht`,
              `${s.automato_total} in Automato · ${s.netbox_labels} of ${s.netbox_total} cables in NetBox with a number · ${s.differs} differ · ${s.mismatch} neighbor does not match`,
            )}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select className="sel" id="cabling-labels-state" aria-label={L("Herkunft", "Origin")} value={state} onChange={(e) => setState(e.target.value)}>
            {STATE_FILTER.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <input className="input" type="search" id="cabling-labels-q" aria-label={L("Suchen", "Search")} placeholder={L("Gerät, Port, Nummer", "Device, port, number")} value={q} onChange={(e) => setQ(e.target.value)} />
          {canEdit && (
            <button className="btn btn-primary btn-sm" id="cabling-labels-new" onClick={() => setEdit({ row: null })}>
              {L("Kabelnummer erfassen", "Capture cable number")}
            </button>
          )}
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title={L("keine Kabelnummern", "no cable numbers")}>
            {L(
              "Nummern kommen aus NetBox (Label der Kabel, mit der Referenzquelle gelesen) bzw. werden hier oder bei den Verbindungen erfasst.",
              "Numbers come from NetBox (label of the cables, read with the reference source) or are captured here or at the links.",
            )}
          </Empty>
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Ende A", "End A")}</th>
                <th />
                <th>{L("Ende B", "End B")}</th>
                <th>{L("Kabelnummer", "Cable number")}</th>
                <th>{L("CDP/LLDP", "CDP/LLDP")}</th>
                <th>NetBox</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.pair} data-pair={r.pair} className={r.neighbor === "mismatch" ? "row-crit" : undefined}>
                  <td className="nowrap">
                    <b>{r.a.device}</b> <span className="mono">{r.a.port}</span> {r.a.free && <FreeChip />}
                  </td>
                  <td className="faint">↔</td>
                  <td className="nowrap">
                    <b>{r.b.device}</b> <span className="mono">{r.b.port}</span> {r.b.free && <FreeChip />}
                  </td>
                  <td>
                    <LabelCell info={r} onEdit={canEdit ? () => setEdit({ row: r }) : undefined} />
                  </td>
                  <td>
                    {r.neighbor === "mismatch" ? (
                      <span className="crit">
                        {L("passt nicht: ", "does not match: ")}
                        {r.wrong
                          .map((w) => `${w.at.device} ${w.at.port} → ${w.seen.map((x) => `${x.device} ${x.port ?? "?"}`).join(", ")}`)
                          .join("; ")}
                      </span>
                    ) : r.neighbor === "ok" ? (
                      <span className="chip ok">{L("passt", "matches")}</span>
                    ) : r.link ? (
                      <span className="faint">{r.link.protocols.join("/").toUpperCase()}</span>
                    ) : (
                      <span className="faint" title={L("keine Nachbar-Daten an beiden Enden", "no neighbor data at either end")}>
                        {L("unbekannt", "unknown")}
                      </span>
                    )}
                  </td>
                  <td className="faint">
                    {r.netbox.map((c) =>
                      c.url ? (
                        <a key={c.id} href={c.url} target="_blank" rel="noreferrer">
                          #{c.id}
                        </a>
                      ) : (
                        <span key={c.id}>#{c.id}</span>
                      ),
                    )}
                    {r.netbox.length === 0 && <Dash />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {edit && (
        <CableLabelDialog
          tenantId={tenantId}
          a={edit.row?.a ?? {}}
          b={edit.row?.b ?? {}}
          info={edit.row}
          fixed={edit.row !== null}
          onClose={() => setEdit(null)}
          onSaved={() => {
            setEdit(null);
            setTick((t) => t + 1);
          }}
        />
      )}
    </div>
  );
}
