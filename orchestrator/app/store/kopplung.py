"""Kopplung mit einer Gegenstelle ueber einen Einmal-Code (1.13.0, ADR 0057).

Anwender 2026-11-16 (Cockpit ADR 0011, Stufe C): „Automato mit dem Cockpit koppeln – bisher
Token kopieren (`aufbau.sh cockpit-token`) – künftig Kopplung mit Einmal-Code“.

Ablauf: ein globaler Admin erzeugt in der Oberflaeche (bzw. auf der VM `aufbau.sh
cockpit-kopplung`) einen **Einmal-Code** und gibt ihn in der Gegenstelle ein; die Gegenstelle
loest ihn ohne andere Anmeldung ein (`POST /kopplung`) und bekommt dafuer **einmal** ein neues
Token. Automato haelt Code und Token nur als SHA-256-Hash.

Grenzen (im Vertrag der Gegenstelle festgehalten):
- Code: 12 Zeichen Crockford-Base32 (60 Bit, angezeigt als `XXXX-XXXX-XXXX`), gueltig
  `CODE_MINUTEN`, einmal, hoechstens `MAX_FEHLVERSUCHE` Fehlversuche, dann ungueltig; je
  Gegenstelle hoechstens ein offener Code (ein neuer ersetzt ihn).
- Token: 32 Byte zufaellig, URL-sicher; ein neues Koppeln ersetzt das bisherige, Entkoppeln
  macht es ungueltig.
- Ob kein Code offen, der Code falsch, abgelaufen, schon benutzt oder gesperrt ist, verraet die
  Antwort nicht (`Abgelehnt` ohne Grund).

Wiederverwendbar je Gegenstelle (`peer`): heute nur das Cockpit, spaeter z. B. der Agent des
NetBox-Hosts - Audit-Aktionen `<peer>.kopplung.*`, ohne Code und Token.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.models import Coupling

log = logging.getLogger(__name__)

COCKPIT = "cockpit"
PEERS = (COCKPIT,)
#: Fassung der Antwort von `POST /kopplung` (Vertrag: `docs/vertraege/kopplung.md` im Cockpit)
VERTRAG = 1

# Crockford-Base32: ohne I, L, O, U - beim Abtippen eindeutig (I/L -> 1, O -> 0)
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VERWECHSELT = str.maketrans({"O": "0", "I": "1", "L": "1"})
CODE_LAENGE = 12  # 12 x 5 Bit = 60 Bit
CODE_GRUPPE = 4
CODE_MINUTEN = 15
MAX_FEHLVERSUCHE = 5
TOKEN_BYTES = 32
ZULETZT_TAKT = timedelta(minutes=5)  # „zuletzt benutzt“ hoechstens so oft schreiben
NAME_LAENGE = 100


class Abgelehnt(Exception):
    """Code nicht einloesbar - bewusst ohne Grund (nichts verraten)."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    """SQLite liefert Zeiten ohne Zone - als UTC lesen."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def normalize_code(raw: str) -> str | None:
    """Code wie eingegeben -> 12 Zeichen des Alphabets (gross, ohne Leerzeichen und `-`,
    O/I/L wie 0/1); None, wenn er so nicht stimmen kann."""
    code = "".join(raw.split()).replace("-", "").upper().translate(_VERWECHSELT)
    if len(code) != CODE_LAENGE or any(c not in ALPHABET for c in code):
        return None
    return code


def format_code(code: str) -> str:
    return "-".join(code[i : i + CODE_GRUPPE] for i in range(0, len(code), CODE_GRUPPE))


def check_name(raw: object) -> str | None:
    """Name der Gegenstelle (nur Anzeige und Audit): 1-100 druckbare Zeichen, sonst None."""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    return name if 0 < len(name) <= NAME_LAENGE and name.isprintable() else None


def _row(session: Session, peer: str, lock: bool = False) -> Coupling | None:
    stmt = select(Coupling).where(Coupling.peer == peer)
    if lock:  # Postgres: Einloesen und Fehlversuche nacheinander (SQLite: ohne Wirkung)
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def _ensure(session: Session, peer: str) -> Coupling:
    if peer not in PEERS:
        raise ValueError(f"unbekannte Gegenstelle: {peer}")
    row = _row(session, peer, lock=True)
    if row is None:
        try:  # erste Kopplung; legt sie gleichzeitig jemand an, gilt seine Zeile
            with session.begin_nested():
                session.add(Coupling(peer=peer, code_failures=0))
        except IntegrityError:
            pass
        row = _row(session, peer, lock=True)
    if row is None:  # pragma: no cover - nur bei einer gleichzeitig wieder geloeschten Zeile
        raise RuntimeError(f"Kopplung {peer} nicht angelegt")
    return row


def _clear_code(row: Coupling) -> None:
    row.code_hash, row.code_expires, row.code_failures, row.code_by = None, None, 0, None


def create_code(
    session: Session, peer: str, actor: str, now: datetime | None = None
) -> tuple[str, datetime]:
    """Neuer Einmal-Code (ersetzt einen offenen) -> (Code als `XXXX-XXXX-XXXX`, gueltig bis).
    Der Code steht danach nur noch als Hash in der Datenbank; der Aufrufer committet."""
    now = now or datetime.now(UTC)
    row = _ensure(session, peer)
    replaced = row.code_hash is not None and (_aware(row.code_expires) or now) > now
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LAENGE))
    expires = now + timedelta(minutes=CODE_MINUTEN)
    row.code_hash, row.code_expires, row.code_failures, row.code_by = (
        _hash(code), expires, 0, actor[:200])  # fmt: skip
    audit.record(session, actor=actor, action=f"{peer}.kopplung.code", subject=peer,
                 detail={"bis": expires.isoformat(), "ersetzt": replaced})  # fmt: skip
    return format_code(code), expires


def deposit_code(
    session: Session, peer: str, raw_code: str, actor: str, now: datetime | None = None
) -> datetime:
    """Einen vorgegebenen Einmal-Code als offenen Code hinterlegen (1.16.0, ADR 0061): beim
    Einrichten einer eigenen Automato-Instanz erzeugt das Cockpit den Code und gibt ihn im Auftrag
    mit (`betrieb.sh installieren --kopplung`); danach gelten dieselben Regeln wie fuer einen hier
    erzeugten (`CODE_MINUTEN`, einmal, `MAX_FEHLVERSUCHE`, nur der Hash bleibt). Ersetzt einen
    offenen Code; wirft ValueError bei einem ungueltigen Code. Der Aufrufer committet."""
    code = normalize_code(raw_code)
    if code is None:
        raise ValueError("ungueltiger Einmal-Code")
    now = now or datetime.now(UTC)
    row = _ensure(session, peer)
    replaced = row.code_hash is not None and (_aware(row.code_expires) or now) > now
    expires = now + timedelta(minutes=CODE_MINUTEN)
    row.code_hash, row.code_expires, row.code_failures, row.code_by = (
        _hash(code), expires, 0, actor[:200])  # fmt: skip
    audit.record(session, actor=actor, action=f"{peer}.kopplung.code", subject=peer,
                 detail={"bis": expires.isoformat(), "ersetzt": replaced})  # fmt: skip
    return expires


def withdraw_code(session: Session, peer: str, actor: str) -> bool:
    """Offenen Code verwerfen -> True, wenn einer offen war."""
    row = _row(session, peer, lock=True)
    if row is None or row.code_hash is None:
        return False
    _clear_code(row)
    audit.record(session, actor=actor, action=f"{peer}.kopplung.code_verworfen", subject=peer)
    return True


def redeem(
    session: Session, peer: str, raw_code: str, name: str, now: datetime | None = None
) -> tuple[str, bool]:
    """Code einloesen -> (neues Token im Klartext, bisheriges Token ersetzt?). `Abgelehnt` ohne
    Grund, wenn kein Code offen, er falsch, abgelaufen oder gesperrt ist. Ein Fehlversuch zaehlt
    nur gegen einen offenen Code; der Aufrufer committet auch nach `Abgelehnt`."""
    now = now or datetime.now(UTC)
    row = _row(session, peer, lock=True)
    if row is None or row.code_hash is None:
        log.warning("Kopplung %s: Code eingegeben, aber keiner offen", peer)
        raise Abgelehnt
    if (_aware(row.code_expires) or now) <= now or row.code_failures >= MAX_FEHLVERSUCHE:
        _clear_code(row)
        audit.record(session, actor="anonym", action=f"{peer}.kopplung.abgelaufen", subject=peer)
        log.warning("Kopplung %s: Code abgelaufen", peer)
        raise Abgelehnt
    code = normalize_code(raw_code)
    if code is None or not hmac.compare_digest(_hash(code), row.code_hash):
        row.code_failures += 1
        gesperrt = row.code_failures >= MAX_FEHLVERSUCHE
        audit.record(session, actor="anonym", action=f"{peer}.kopplung.fehlversuch", subject=peer,
                     detail={"versuch": row.code_failures, "gesperrt": gesperrt})  # fmt: skip
        log.warning("Kopplung %s: falscher Code (Versuch %d von %d)", peer, row.code_failures,
                    MAX_FEHLVERSUCHE)  # fmt: skip
        if gesperrt:
            _clear_code(row)
        raise Abgelehnt
    replaced = row.token_hash is not None
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row.token_hash, row.peer_name, row.coupled_at = _hash(token), name[:NAME_LAENGE], now
    row.coupled_by, row.last_used = row.code_by, None
    _clear_code(row)
    audit.record(session, actor=peer, action=f"{peer}.kopplung.gekoppelt", subject=peer,
                 detail={"name": row.peer_name, "ersetzt": replaced,
                         "code_von": row.coupled_by})  # fmt: skip
    log.info("Kopplung %s: gekoppelt mit %r", peer, row.peer_name)
    return token, replaced


def uncouple(session: Session, peer: str, actor: str) -> bool:
    """Entkoppeln: das gekoppelte Token gilt ab sofort nicht mehr -> True, wenn gekoppelt war."""
    row = _row(session, peer, lock=True)
    if row is None or row.token_hash is None:
        return False
    name = row.peer_name
    row.token_hash, row.peer_name, row.coupled_at, row.coupled_by, row.last_used = (
        None, None, None, None, None)  # fmt: skip
    audit.record(session, actor=actor, action=f"{peer}.kopplung.entkoppelt", subject=peer,
                 detail={"name": name})  # fmt: skip
    return True


def coupled(session: Session, peer: str) -> bool:
    return session.scalar(select(Coupling.token_hash).where(Coupling.peer == peer)) is not None


def token_valid(session: Session, peer: str, token: str, now: datetime | None = None) -> bool:
    """Gilt `token` als gekoppeltes Token? Fuehrt „zuletzt benutzt“ hoechstens alle
    `ZULETZT_TAKT` nach (der Aufrufer committet mit seiner Anfrage)."""
    row = _row(session, peer)
    if row is None or row.token_hash is None or not token:
        return False
    if not hmac.compare_digest(_hash(token), row.token_hash):
        return False
    now = now or datetime.now(UTC)
    last = _aware(row.last_used)
    if last is None or now - last >= ZULETZT_TAKT:
        row.last_used = now
    return True


def status(session: Session, peer: str, now: datetime | None = None) -> dict:
    """Stand fuer die Oberflaeche - ohne Hashes."""
    now = now or datetime.now(UTC)
    row = _row(session, peer)
    expires = _aware(row.code_expires) if row is not None else None
    open_code = row is not None and row.code_hash is not None and expires is not None
    open_code = open_code and expires > now and row.code_failures < MAX_FEHLVERSUCHE
    coupled_now = row is not None and row.token_hash is not None

    def iso(value: datetime | None) -> str | None:
        value = _aware(value)
        return value.isoformat() if value is not None else None

    return {
        "peer": peer,
        "coupled": coupled_now,
        "name": row.peer_name if coupled_now else None,
        "coupled_at": iso(row.coupled_at) if coupled_now else None,
        "coupled_by": row.coupled_by if coupled_now else None,
        "last_used": iso(row.last_used) if coupled_now else None,
        "code": {"expires": iso(expires), "failures": row.code_failures} if open_code else None,
        "code_minutes": CODE_MINUTEN,
        "max_failures": MAX_FEHLVERSUCHE,
    }
