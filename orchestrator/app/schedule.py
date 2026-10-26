"""Zeitplan eines Quellsystems als Cron-Ausdruck (5 Felder, pure Logik).

`Minute Stunde Tag Monat Wochentag`, wie crontab: `*`, `*/n`, `a-b`, `a-b/n`, Listen `a,b`;
Wochentag 0–7 (0 und 7 = Sonntag) oder `mon`…`sun`, Monat auch `jan`…`dec`. Sind Tag *und*
Wochentag eingeschraenkt, genuegt einer von beiden (wie cron). Die Zeit wird in der
Zeitzone des Aufrufers ausgewertet (Orchestrator: `scheduler_timezone`, Standard
Europe/Zurich) - Sommer-/Winterzeit wie die lokale Uhr.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

_FIELDS = (  # Name, Minimum, Maximum
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 7),
)
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DAYS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}
_LOOKAHEAD_DAYS = 400  # weiter sucht weder previous() noch next_after()


@dataclass(frozen=True)
class Cron:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sonntag
    day_any: bool
    weekday_any: bool

    def _day_ok(self, dt: datetime) -> bool:
        if dt.month not in self.months:
            return False
        dom = dt.day in self.days
        dow = (dt.isoweekday() % 7) in self.weekdays
        if self.day_any or self.weekday_any:
            return dom and dow
        return dom or dow

    def matches(self, dt: datetime) -> bool:
        return self._day_ok(dt) and dt.hour in self.hours and dt.minute in self.minutes

    def previous(self, now: datetime) -> datetime | None:
        """Letzter Termin <= now (minutengenau, gleiche Zeitzone wie now)."""
        t = now.replace(second=0, microsecond=0)
        limit = t - timedelta(days=_LOOKAHEAD_DAYS)
        while t >= limit:
            if not self._day_ok(t):
                t = t.replace(hour=0, minute=0) - timedelta(minutes=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) - timedelta(minutes=1)
            elif t.minute not in self.minutes:
                t -= timedelta(minutes=1)
            else:
                return t
        return None

    def next_after(self, now: datetime) -> datetime | None:
        """Naechster Termin > now."""
        t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = t + timedelta(days=_LOOKAHEAD_DAYS)
        while t <= limit:
            if not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        return None


def _value(token: str, field: str) -> int:
    names = _MONTHS if field == "month" else _DAYS if field == "weekday" else {}
    low = token.lower()
    if low in names:
        return names[low]
    if not token.isdigit():
        raise ValueError(f"{field}: '{token}' ist keine Zahl")
    return int(token)


def _field(spec: str, field: str, lo: int, hi: int) -> frozenset[int]:
    values: set[int] = set()
    for part in spec.split(","):
        base, _, step_s = part.partition("/")
        step = int(step_s) if step_s.isdigit() and int(step_s) > 0 else None
        if step_s and step is None:
            raise ValueError(f"{field}: Schrittweite '{step_s}' ungültig")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, field), _value(b, field)
        else:
            start = _value(base, field)
            end = hi if step else start
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise ValueError(f"{field}: '{part}' ausserhalb {lo}–{hi}")
        values.update(range(start, end + 1, step or 1))
    if field == "weekday" and 7 in values:
        values = (values - {7}) | {0}
    return frozenset(values)


def parse(expr: str) -> Cron:
    """Cron-Ausdruck pruefen und zerlegen; ValueError mit verstaendlichem Grund."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("Zeitplan braucht 5 Felder: Minute Stunde Tag Monat Wochentag")
    sets = [_field(p, name, lo, hi) for p, (name, lo, hi) in zip(parts, _FIELDS, strict=True)]
    return Cron(
        expr=" ".join(parts),
        minutes=sets[0],
        hours=sets[1],
        days=sets[2],
        months=sets[3],
        weekdays=sets[4],
        day_any=parts[2] == "*",
        weekday_any=parts[4] == "*",
    )
