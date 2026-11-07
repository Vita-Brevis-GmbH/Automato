// UI-Tests lokal in mehreren Shards gleichzeitig: je Shard ein eigener Playwright-Lauf mit
// eigenem Orchestrator + Vite (eigene Ports, eigene SQLite-Datei, siehe playwright.config.ts) -
// die Shards teilen keine Daten, innerhalb eines Shards laufen die Tests wie bei `npm run e2e`
// nacheinander.
//
//   npm run e2e:shards                     # Kerne - 1 Shards (2 bis 4; 4 Kerne: 3)
//   npm run e2e:shards -- 3                # drei Shards
//   npm run e2e:shards -- 2 -g "Zonen"     # weitere Argumente gehen an Playwright
//
// Ports je Shard n: Orchestrator 8100+n, Vite 5200+n; Ergebnisse in test-results/shard-n.
// Gemessen auf 4 Kernen: 1 Lauf 5:20 min, 2 Shards 3:33, 3 Shards 2:57, 4 Shards 2:58.
import { spawn } from "node:child_process";
import { availableParallelism, tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const total = /^\d+$/.test(args[0] ?? "")
  ? Number(args.shift())
  : Math.min(4, Math.max(2, availableParallelism() - 1));
if (total < 1) throw new Error("Anzahl Shards mindestens 1");

const started = Date.now();
const shard = (n) =>
  new Promise((resolve) => {
    const env = {
      ...process.env,
      E2E_API_PORT: String(8100 + n),
      E2E_PORT: String(5200 + n),
      E2E_DB: join(tmpdir(), `automato-e2e-shard${n}.db`),
    };
    const child = spawn(
      "npx",
      ["playwright", "test", `--shard=${n}/${total}`, `--output=test-results/shard-${n}`, ...args],
      { env, stdio: ["ignore", "pipe", "pipe"] },
    );
    const tag = `[${n}/${total}] `;
    for (const stream of [child.stdout, child.stderr]) {
      let rest = "";
      stream.on("data", (chunk) => {
        const lines = (rest + chunk).split("\n");
        rest = lines.pop();
        for (const line of lines) process.stdout.write(tag + line + "\n");
      });
      stream.on("end", () => rest && process.stdout.write(tag + rest + "\n"));
    }
    child.on("close", (code) => resolve(code ?? 1));
  });

const codes = await Promise.all(Array.from({ length: total }, (_, i) => shard(i + 1)));
const seconds = Math.round((Date.now() - started) / 1000);
for (const [i, code] of codes.entries())
  console.log(`Shard ${i + 1}/${total}: ${code === 0 ? "ok" : `fehlgeschlagen (Exit ${code})`}`);
console.log(`Dauer ${Math.floor(seconds / 60)} min ${seconds % 60} s`);
process.exit(codes.every((code) => code === 0) ? 0 : 1);
