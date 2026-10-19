import { Icon, Note, PageHead } from "./ui";

// Netzwerkdesign (Mockup-Ansicht): geplantes optionales Modul, noch ohne Funktion.
export default function Design() {
  const tools = ["M4 4h16v16H4z", "M12 2v20M2 12h20", "M4 12h16", "M7 7h10v10H7z", "M3 12a9 9 0 0 1 18 0"];
  return (
    <>
      <PageHead
        title={
          <>
            Netzwerkdesign <span className="badge-soon">geplant</span>
          </>
        }
      >
        Netzwerkpläne direkt in Automato zeichnen (draw.io-Integration) und mit Regeln/Zonen verknüpfen.
      </PageHead>
      <Note>
        Optionales Modul, noch nicht umgesetzt: eingebetteter draw.io-Editor. Objekte lassen sich später mit
        Zonen/VIPs aus dem Store verknüpfen, Diagramme werden pro Mandant versioniert.
      </Note>
      <div className="draw-wrap">
        <div className="draw-tools">
          {tools.map((p) => (
            <span className="draw-tool" key={p}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d={p} />
              </svg>
            </span>
          ))}
          <div style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" disabled>
            Speichern
          </button>
        </div>
        <div className="draw-canvas">
          <div>
            <Icon name="design" size={22} />
            <div className="big" style={{ fontWeight: 600, color: "var(--fg)", marginTop: 6 }}>
              Zeichenfläche folgt mit dem Modul
            </div>
            <div className="faint">Bis dahin: Regeln, NAT und Applikations-Doku nutzen.</div>
          </div>
        </div>
      </div>
    </>
  );
}
