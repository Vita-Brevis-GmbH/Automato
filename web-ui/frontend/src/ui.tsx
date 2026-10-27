// Gemeinsame Darstellungs-Bausteine: Icons, Chips/Badges, Seitenkopf, Fehler, Modal.
import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { describeError } from "./api";
import { L, locale } from "./i18n";

const ICONS: Record<string, ReactNode> = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="9" />
      <rect x="14" y="3" width="7" height="5" />
      <rect x="14" y="12" width="7" height="9" />
      <rect x="3" y="16" width="7" height="5" />
    </>
  ),
  rules: <path d="M3 6h18M3 12h18M3 18h18" />,
  nat: <path d="M4 7h11l-3-3m3 3-3 3M20 17H9l3-3m-3 3 3 3" />,
  apps: (
    <>
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </>
  ),
  findings: (
    <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  ),
  design: (
    <>
      <rect x="3" y="3" width="6" height="6" />
      <rect x="15" y="15" width="6" height="6" />
      <path d="M9 6h6a3 3 0 0 1 3 3v6" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20a6 6 0 0 1 12 0M16 7a3 3 0 0 1 0 6m5 7a5 5 0 0 0-3.5-4.8" />
    </>
  ),
  tenants: <path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6" />,
  // Kunden (ADR 0018): Ausschnitte der Umgebung
  customers: (
    <>
      <rect x="3" y="3" width="8" height="8" rx="1" />
      <rect x="13" y="3" width="8" height="8" rx="1" />
      <rect x="3" y="13" width="8" height="8" rx="1" />
      <path d="M13 17h8M17 13v8" />
    </>
  ),
  dict: (
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  ),
  agents: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </>
  ),
  server: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="11" width="16" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3-3" />
    </>
  ),
  close: <path d="m6 6 12 12M18 6 6 18" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5m0 3h.01" />
    </>
  ),
  download: <path d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14" />,
  play: <path d="M7 4v16l13-8z" />,
  chevron: <path d="m6 9 6 6 6-6" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  objects: (
    <>
      <path d="M12 3 3 7.5l9 4.5 9-4.5z" />
      <path d="m3 12 9 4.5 9-4.5M3 16.5 12 21l9-4.5" />
    </>
  ),
  reconcile: <path d="M4 9h13l-3-3m3 3-3 3M20 15H7l3-3m-3 3 3 3" />,
  route: (
    <>
      <circle cx="6" cy="18" r="2" />
      <circle cx="18" cy="6" r="2" />
      <path d="M6 16V10a4 4 0 0 1 4-4h6M18 8v6a4 4 0 0 1-4 4H8" />
    </>
  ),
  upload: <path d="M12 16V4m0 0-4 4m4-4 4 4M5 20h14" />,
  cable: (
    <>
      <rect x="3" y="4" width="6" height="5" rx="1" />
      <rect x="15" y="15" width="6" height="5" rx="1" />
      <path d="M6 9v3a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3" />
    </>
  ),
  path: (
    <>
      <circle cx="5" cy="19" r="2" />
      <path d="M7 19h6a3 3 0 0 0 0-6h-2a3 3 0 0 1 0-6h6" />
      <path d="M19 3v8M19 3l3 2-3 2" />
    </>
  ),
};

export function Icon({ name, size = 16, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}

const HELP_KEY = "automato_page_help";
function readHelp(): boolean {
  try {
    return localStorage.getItem(HELP_KEY) === "open";
  } catch {
    return false;
  }
}

export function PageHead({
  title,
  children,
  actions,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  // Kopf bleibt beim Scrollen stehen; seine Höhe braucht der Reiter darunter (.tabbar.sticky)
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const main = el?.closest<HTMLElement>(".main");
    if (!el || !main) return;
    const set = () => main.style.setProperty("--page-head-h", `${el.offsetHeight}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Erklärung unter dem Titel (0.75.0, Anwender: zu viel Info): eine Zeile, Klick klappt auf
  // bzw. zu – gilt für alle Seiten und bleibt im Browser
  const [help, setHelp] = useState(() => readHelp());
  const toggle = (e: React.MouseEvent) => {
    if ((e.target as Element).closest("a")) return; // Links bleiben Links
    const next = !help;
    try {
      localStorage.setItem(HELP_KEY, next ? "open" : "closed");
    } catch {
      /* localStorage nicht verfügbar */
    }
    setHelp(next);
  };
  return (
    <div className="page-head" ref={ref}>
      <div>
        <h1>{title}</h1>
        {children && (
          <p
            className={`page-help${help ? " open" : ""}`}
            title={
              help ? L("Erklärung zuklappen", "Collapse explanation") : L("Erklärung aufklappen", "Expand explanation")
            }
            onClick={toggle}
          >
            {children}
          </p>
        )}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

// Kunde oben gewählt (0.76.0): Element gehört zum geteilten Teil der Umgebung
export function SharedChip({ shared }: { shared?: boolean }) {
  if (!shared) return null;
  return (
    <span
      className="chip na shared-chip"
      title={L(
        "geteilter Teil der Umgebung – gehört nicht dem gewählten Kunden allein (z. B. Tenant common, VDOM für mehrere Kunden)",
        "shared part of the environment – does not belong to the selected customer alone (e.g. tenant common, VDOM for several customers)",
      )}
    >
      {L("geteilt", "shared")}
    </span>
  );
}

export function Note({ children, warn }: { children: ReactNode; warn?: boolean }) {
  return (
    <div className={`note ${warn ? "warn" : ""}`}>
      <span className="ico">
        <Icon name="info" size={15} />
      </span>
      <div>{children}</div>
    </div>
  );
}

// Fehleranzeige: nimmt einen Fehler (ApiError/Error/string) und zeigt eine verständliche Meldung.
export function ErrorBox({
  error,
  prefix,
  onClose,
}: {
  error: unknown;
  prefix?: string;
  onClose?: () => void;
}) {
  if (!error) return null;
  const text = typeof error === "string" ? error : describeError(error);
  return (
    <div className="error" role="alert">
      <span className="ico">
        <Icon name="findings" size={15} />
      </span>
      <div>
        {prefix && <b>{prefix}: </b>}
        {text}
      </div>
      {onClose && (
        <button className="linkbtn x" onClick={onClose} aria-label={L("Schliessen", "Close")}>
          ✕
        </button>
      )}
    </div>
  );
}

// Geräte-Zugangsdaten dürfen nie vom Browser/Passwort-Manager befüllt werden – sonst landen beim
// Bearbeiten eines Quellsystems gespeicherte (fremde) Logins als neue Zugangsdaten im Orchestrator.
// autoComplete="off" ignorieren Chrome/Edge bei Passwortfeldern; "new-password" + schreibgeschützt
// bis zum Fokus verhindern das Befüllen, die data-Attribute halten gängige Passwort-Manager fern.
const NO_FILL = { "data-1p-ignore": "true", "data-lpignore": "true", "data-bwignore": "true", "data-form-type": "other" };

// Textfeld für Geräte-Benutzernamen (APIC/Infoblox) ohne Autofill.
export function CredentialInput({
  id,
  value,
  onChange,
  placeholder,
  disabled,
  maxLength,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  maxLength?: number;
}) {
  const [armed, setArmed] = useState(false);
  return (
    <input
      id={id}
      name={`${id}-automato`}
      className="input mono"
      value={value}
      autoComplete="off"
      readOnly={!armed}
      onFocus={() => setArmed(true)}
      maxLength={maxLength}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      {...NO_FILL}
    />
  );
}

// Passwortfeld mit "Zeigen/Verbergen" für Tokens (Wert nie vorbefüllt vom Server, nie Autofill).
export function SecretInput({
  id,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [show, setShow] = useState(false);
  const [armed, setArmed] = useState(false);
  return (
    <div style={{ display: "flex", gap: 6 }}>
      <input
        id={id}
        name={`${id}-automato`}
        className="input mono"
        type={show ? "text" : "password"}
        value={value}
        autoComplete="new-password"
        readOnly={!armed}
        onFocus={() => setArmed(true)}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        {...NO_FILL}
      />
      <button className="btn btn-ghost btn-sm" type="button" onClick={() => setShow((s) => !s)}>
        {show ? L("Verbergen", "Hide") : L("Zeigen", "Show")}
      </button>
    </div>
  );
}

export function Loading({ children = L("Lade…", "Loading…") }: { children?: ReactNode }) {
  return <div className="loading">{children}</div>;
}

export function Empty({
  title,
  children,
  action,
}: {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="big">{title}</div>
      {children}
      {action && <div>{action}</div>}
    </div>
  );
}

// Liste auf Knopfdruck (0.37.0): unspezifische Regeln (any, Netz) zeigen nur die Regel; welche
// Systeme sie nutzen können, klappt der Knopf auf
export function Expandable({ items, noun }: { items: string[]; noun: [string, string] }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  return (
    <div className="expandable">
      <button
        type="button"
        className="linkbtn"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {open ? "▾" : "▸"} {items.length} {items.length === 1 ? noun[0] : noun[1]}
      </button>
      {open && <div className="faint mono expandable-list">{items.join(", ")}</div>}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean; // breite Maske für Tabellen im Formular (z. B. Selektoren eines Kunden)
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="iconbtn" onClick={onClose} aria-label={L("Schliessen", "Close")}>
            <Icon name="close" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({
  title,
  sub,
  onClose,
  children,
}: {
  title: ReactNode;
  sub?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {sub && <div className="sub">{sub}</div>}
          </div>
          <button className="iconbtn close" onClick={onClose} aria-label={L("Schliessen", "Close")}>
            <Icon name="close" />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

// --- Chips für Status/Aktion/Schwere ---

const MATCH: Record<string, [string, string]> = {
  matched: ["ok", L("gematcht", "matched")],
  unmatched: ["crit", L("nicht aufgelöst", "unresolved")],
  expected_external: ["ext", L("extern (ohne IPAM)", "external (no IPAM)")],
  not_applicable: ["na", "n/a"],
  ambiguous: ["warn", L("mehrdeutig", "ambiguous")],
};

const SEVERITY: Record<string, string> = { error: "crit", warning: "warn", info: "info" };

export function ActionBadge({ action }: { action: string }) {
  const ok = action === "allow" || action === "accept" || action === "permit";
  return <span className={`chip ${ok ? "accept" : "deny"}`}>{action}</span>;
}

export function MatchBadge({ status }: { status: string | null }) {
  if (!status) return <span className="faint">–</span>;
  const [cls, label] = MATCH[status] ?? ["na", status];
  return <span className={`chip ${cls}`}>{label}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`chip ${SEVERITY[severity] ?? "na"}`}>{severity}</span>;
}

const RUN_STATUS: Record<string, [string, string, string]> = {
  queued: ["na", "n", L("wartet", "waiting")],
  running: ["info", "y", L("läuft", "running")],
  completed: ["ok", "g", "OK"],
  failed: ["crit", "r", L("Fehler", "failed")],
};

export function RunStatusChip({ status }: { status: string }) {
  const [cls, dot, label] = RUN_STATUS[status] ?? ["na", "n", status];
  return (
    <span className={`chip ${cls}`}>
      <span className={`dot ${dot}`} />
      {label}
    </span>
  );
}

export function Dash() {
  return <span className="faint">–</span>;
}

export function fmtTime(iso: string | null): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const time = d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return L(`heute ${time}`, `today ${time}`);
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return L(`gestern ${time}`, `yesterday ${time}`);
  return `${d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "2-digit" })} ${time}`;
}

export function download(filename: string, content: string | Blob, type = "text/markdown") {
  const blob = typeof content === "string" ? new Blob([content], { type }) : content;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
