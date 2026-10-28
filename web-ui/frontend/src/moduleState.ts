// Module je Mandant (0.107.0, ADR 0036): was für den gewählten Mandanten aus ist, blendet die Oberfläche aus (Navigation,
// Karten der Doku) – der Server sperrt es zusätzlich (403 module_disabled). Ohne Angabe gilt ein Modul als an.
import { createContext, useContext } from "react";
import type { ModuleInfo, ModuleKey } from "./api";

export const ModulesContext = createContext<ModuleInfo[] | null>(null);
export const MODULES_CHANGED = "automato-modules";

export const moduleOn = (list: ModuleInfo[] | null, key: ModuleKey): boolean => list?.find((m) => m.key === key)?.effective ?? true;

export function useModule(key: ModuleKey): boolean {
  return moduleOn(useContext(ModulesContext), key);
}

// Ansichten der ausgeschalteten Module (die Navigation blendet sie aus)
export const hiddenViews = (list: ModuleInfo[] | null): Set<string> =>
  new Set((list ?? []).filter((m) => !m.effective).flatMap((m) => m.views));
