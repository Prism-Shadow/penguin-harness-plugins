/**
 * The harness-compatibility gate: can every PenguinHarness in the field read the built index?
 *
 *   pnpm check-harness [index.json]      (default: dist/index.json, what `pnpm build` wrote)
 *
 * The document is parsed with the reader of every harness release from the floor up (see
 * harness-readers.ts), each one the release's own published code, and — when HARNESS_MAIN_READER
 * names a harness checkout's `packages/core/src/plugin/index-document.ts` — with the harness's
 * `main` branch too, the reader its next release will ship. One reader refusing it fails the run,
 * and so does one that will not load: a harness refuses the whole document over a single row, so
 * publishing what any of them refuses empties that release's plugin market.
 *
 * CI runs this after `pnpm build` on every pull request, and the Deploy and Nightly workflows run
 * it before they replace the live document.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { READER_EXPORT, READER_FLOOR, READER_PACKAGE } from "./harness-readers.js";
import {
  publishedReaders,
  sourceReader,
  type LoadFailure,
  type Reader,
} from "./lib/load-reader.js";
import { judge, type Verdict } from "./lib/reader-verdict.js";

const file = process.argv[2] ?? path.join(process.env.OUT_DIR ?? "dist", "index.json");
const mainReader = process.env.HARNESS_MAIN_READER;

let document: unknown;
try {
  document = JSON.parse(fs.readFileSync(file, "utf8"));
} catch (err) {
  console.error(`Cannot read ${file}: ${err instanceof Error ? err.message : err}`);
  process.exit(1);
}

/** A GitHub Actions error annotation, so a red run says why on its summary page. */
function annotate(title: string, message: string): void {
  if (process.env.GITHUB_ACTIONS !== "true") return;
  const escape = (text: string) =>
    text.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
  const property = escape(title).replace(/:/g, "%3A").replace(/,/g, "%2C");
  console.log(`::error title=${property}::${escape(message)}`);
}

/** `penguin-harness main (abc1234)`, the commit read from the checkout when it is one. */
function mainLabel(reader: string): string {
  try {
    const commit = execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: path.dirname(reader),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return `penguin-harness main (${commit})`;
  } catch {
    return "penguin-harness main";
  }
}

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-harness-readers-"));
const readers: Reader[] = [];
const failures: LoadFailure[] = [];
try {
  const published = await publishedReaders(READER_PACKAGE, READER_EXPORT, READER_FLOOR, workDir);
  readers.push(...published.readers);
  failures.push(...published.failures);
  if (mainReader !== undefined && mainReader !== "") {
    const label = mainLabel(mainReader);
    try {
      readers.push(await sourceReader(label, mainReader));
    } catch (err) {
      failures.push({ label, error: err instanceof Error ? err.message : String(err) });
    }
  }

  const rows = Array.isArray(document) ? `${document.length} rows` : "not an array";
  console.log(`Reading ${file} (${rows}) as each harness reader would:\n`);
  const verdicts: Verdict[] = readers.map((reader) => judge(document, file, reader));
  for (const verdict of verdicts) {
    if (verdict.refusal === undefined) {
      console.log(`  accepts   ${verdict.reader}`);
      continue;
    }
    const { message, rows: refused } = verdict.refusal;
    console.log(`  REFUSES   ${verdict.reader}: ${message}`);
    if (refused.length > 0) {
      const of = Array.isArray(document) ? ` of ${document.length}` : "";
      console.log(`            it refuses ${refused.length}${of} rows:`);
      for (const row of refused) {
        console.log(`            - row ${row.index}, ${row.row}: ${row.reason}`);
      }
    }
    const first = refused[0];
    annotate(
      `The plugin index is refused by ${verdict.reader}`,
      first === undefined
        ? message
        : `${refused.length} row(s) refused, first row ${first.index} (${first.row}): ${first.reason}`,
    );
  }
  for (const failure of failures) {
    console.log(`  NOT RUN   ${failure.label}: ${failure.error}`);
    annotate(`Harness reader ${failure.label} did not load`, failure.error);
  }
  if (mainReader === undefined || mainReader === "") {
    console.log(
      `\n  (The harness's main branch was not read: set HARNESS_MAIN_READER to a checkout's packages/core/src/plugin/index-document.ts.)`,
    );
  }

  const refusing = verdicts.filter((verdict) => verdict.refusal !== undefined);
  if (refusing.length > 0) {
    console.error(`
This index must not be published. A harness that refuses one row refuses the whole document, so
every remote plugin would vanish from the market of each deployment whose reader refuses it:
${refusing.map((verdict) => `  ${verdict.reader}`).join("\n")}

Change what the build writes (src/build-index.ts, src/lib/channels.ts) or the manifests, so that
every reader above accepts the document: a new field or shape has to be one those readers already
accept. A document that genuinely needs a newer harness can only be published once every release
that refuses it is below READER_FLOOR in src/harness-readers.ts — and raising that floor is a
decision to stop supporting those releases, not a way to turn this check green.`);
  }
  if (failures.length > 0) {
    console.error(`
${failures.length === 1 ? "A harness reader" : `${failures.length} harness readers`} could not be run, and this check does not vouch for a reader it
cannot run. If the registry or GitHub was unreachable, run the job again. If a harness release
moved or changed its reader, update src/lib/load-reader.ts to load it from where it now lives.`);
  }
  // exitCode rather than exit(): the scratch directory still has to go.
  if (refusing.length > 0 || failures.length > 0) process.exitCode = 1;
  else console.log(`\nEvery harness reader accepts ${file}.`);
} finally {
  fs.rmSync(workDir, { recursive: true, force: true });
}
