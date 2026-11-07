// Knopf „Weg-Suche“ an einer Regel (0.112.0, Anwender: „bei den Regeln & Contracts, Firewall Regeln in der Applikation und
// Doku nach Kategorie auch den Knopf Wegsuche anbieten, damit ich einfach nach den Fällen suchen kann“): öffnet die
// Weg-Suche in einem neuen Tab mit Quelle, Ziel und Dienst der Regel (#wege?rule=<id>, die Werte liefert der Server).
import { L } from "../../i18n";
import { useModule } from "../../moduleState";
import { Icon } from "../../ui";

export function RuleWegeLink({ ruleId, compact = false }: { ruleId: number | null | undefined; compact?: boolean }) {
  const on = useModule("wege");
  if (!on || ruleId == null) return null;
  return (
    <a
      className="btn btn-ghost btn-sm rule-wege"
      href={`#wege?rule=${ruleId}`}
      target="_blank"
      rel="noopener"
      onClick={(e) => e.stopPropagation()}
      title={L(
        "Weg-Suche in einem neuen Tab mit Quelle, Ziel und Dienst dieser Regel",
        "Path search in a new tab with source, destination and service of this rule",
      )}
    >
      <Icon name="route" size={14} />
      {compact ? "" : ` ${L("Weg-Suche", "Path search")}`} ↗
    </a>
  );
}
