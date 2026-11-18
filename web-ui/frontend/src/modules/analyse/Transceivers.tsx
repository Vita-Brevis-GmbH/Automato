// Transceiver je Port (1.19.0, ADR 0067: eigene Stelle „Transceiver“ in L1): alle gelesenen Transceiver der Switches mit
// Typ, Hersteller, Teil und Seriennummer, dazu der Lesestatus je Switch (`GET /cabling/transceivers`). Rein lesend.
import { useEffect, useState } from "react";
import { L } from "../../i18n";
import { Empty, ErrorBox, Loading } from "../../ui";
import { TransceiverStatusChip } from "./Kabel";
import { fetchTransceivers } from "./kabelApi";

type Data = Awaited<ReturnType<typeof fetchTransceivers>>;

export function TransceiversTab({ tenantId, initialQ = "" }: { tenantId: number; initialQ?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [q, setQ] = useState(initialQ);
  useEffect(() => {
    let live = true;
    fetchTransceivers(tenantId)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId]);
  if (err) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (data === null) return <Loading />;
  const needle = q.trim().toLowerCase();
  const items = data.items.filter(
    (t) => !needle || [t.device, t.port, t.type, t.vendor, t.part, t.serial].some((v) => (v ?? "").toLowerCase().includes(needle)),
  );
  const unread = data.devices.filter((d) => d.status !== "read");
  return (
    <>
      <div className="card" id="transceiver-devices">
        <div className="card-head">
          <h2>{L("Lesestatus je Switch", "Read status per switch")}</h2>
          <span className="faint">
            {L(
              `${data.devices.length - unread.length} von ${data.devices.length} gelesen`,
              `${data.devices.length - unread.length} of ${data.devices.length} read`,
            )}
          </span>
        </div>
        <div className="card-body" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {data.devices.length === 0 ? (
            <span className="faint">{L("keine Switches gelesen", "no switches read")}</span>
          ) : (
            data.devices.map((d) => (
              <span key={d.device} className="transceiver-device">
                <b>{d.device}</b> <TransceiverStatusChip status={d.status} count={d.count} />
              </span>
            ))
          )}
        </div>
      </div>
      <div className="card" id="transceivers" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>{L("Transceiver", "Transceivers")}</h2>
          <input
            id="transceiver-q"
            className="input input-inline"
            placeholder={L("Switch, Port, Typ, Seriennummer …", "Switch, port, type, serial number …")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="card-body scroll">
          {items.length === 0 ? (
            <Empty title={L("Keine Transceiver", "No transceivers")}>
              <span className="faint">
                {needle
                  ? L("Für diesen Filter gibt es keine.", "There are none for this filter.")
                  : L("Die gelesenen Switches melden keine Transceiver.", "The read switches report no transceivers.")}
              </span>
            </Empty>
          ) : (
            <table className="compact">
              <thead>
                <tr>
                  <th>Switch</th>
                  <th>Port</th>
                  <th>{L("Typ", "Type")}</th>
                  <th>{L("Hersteller", "Vendor")}</th>
                  <th>{L("Teil", "Part")}</th>
                  <th>{L("Seriennummer", "Serial number")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr key={`${t.device}|${t.port}`}>
                    <td>{t.device}</td>
                    <td className="mono">{t.port}</td>
                    <td>{t.type ?? "–"}</td>
                    <td>{t.vendor ?? "–"}</td>
                    <td>{t.part ?? "–"}</td>
                    <td className="mono">{t.serial ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
