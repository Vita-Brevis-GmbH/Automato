// Verzeichnis der Ansichten (0.110.0, ADR 0039): je Modul ein Ordner mit seinen Ansichten; die Shell (App.tsx) baut
// Navigation und Anzeige daraus. Reihenfolge nach `order`. Ein Modul ohne Ansicht (Gateway) hat keinen Ordner.
import { VIEWS as analyse } from "./analyse";
import { VIEWS as design } from "./design";
import { VIEWS as doku } from "./doku";
import { VIEWS as einstellungen } from "./einstellungen";
import { VIEWS as findings } from "./findings";
import { VIEWS as kern } from "./kern";
import { VIEWS as schreibpfad } from "./schreibpfad";
import type { ViewEntry } from "./types";
import { VIEWS as wege } from "./wege";
import { VIEWS as worker } from "./worker";

export const VIEWS: ViewEntry[] = [
  ...kern,
  ...einstellungen,
  ...worker,
  ...analyse,
  ...wege,
  ...findings,
  ...doku,
  ...design,
  ...schreibpfad,
].sort((a, b) => a.order - b.order);

export type { Stats, View, ViewEntry, ViewParams, ViewProps } from "./types";
