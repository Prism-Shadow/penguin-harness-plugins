/**
 * Whether one harness reader accepts a built index — and when it does not, which rows it refuses
 * and over what, in words an author can act on.
 *
 * The readers in the field say very little: 0.2.19's names only the position of the first row it
 * refuses ("malformed entry at index 3"). So beyond the reader's own words, every row is read on
 * its own to list each one the reader refuses, and a refused row is read again without each of
 * its fields in turn — and, when the field is `channels`, without each channel — to name what the
 * reader objects to. A reader that takes the row without `channels[1]` has said that `channels[1]`
 * is what it refuses: the reason is the reader's own judgement, never a second opinion of what the
 * schema ought to be.
 */
import type { Reader } from "./load-reader.js";

/** How a deployment's server reads a remote index: a remote row's icon is dropped, not served. */
const READ_AS_REMOTE = { icons: "drop" } as const;

/** Longest field value quoted in a reason; a description can run to a thousand characters. */
const MAX_QUOTED = 120;

/** One row the reader refuses. */
export interface RowRefusal {
  /** The row's position in the document. */
  index: number;
  /** The row's `name@version`, as the row spells them. */
  row: string;
  /** What the reader refuses about it. */
  reason: string;
}

/** One reader's verdict on a document. */
export interface Verdict {
  reader: string;
  /** Absent when the reader accepts the document. */
  refusal?: {
    /** The reader's own words for the whole document. */
    message: string;
    /** Every row it refuses on its own; empty when only the document as a whole is refused. */
    rows: RowRefusal[];
  };
}

/** The reader's error for `data`, or null when it accepts it. */
function refusalOf(reader: Reader, data: unknown, source: string): string | null {
  try {
    reader.parse(data, source, READ_AS_REMOTE);
    return null;
  } catch (err) {
    return err instanceof Error ? err.message : String(err);
  }
}

function rowName(row: unknown): string {
  if (typeof row !== "object" || row === null) return "(not an object)";
  const { name, version } = row as Record<string, unknown>;
  const spelled = (value: unknown) => (typeof value === "string" ? value : "?");
  return `${spelled(name)}@${spelled(version)}`;
}

function quoted(value: unknown): string {
  const text = JSON.stringify(value) ?? String(value);
  return text.length > MAX_QUOTED ? `${text.slice(0, MAX_QUOTED - 1)}…` : text;
}

/** What a refused row is refused over: the fields the reader takes it without, or its words. */
function reasonFor(reader: Reader, row: unknown, words: string): string {
  if (typeof row !== "object" || row === null || Array.isArray(row)) {
    return `the row is not an object; the reader says: ${words}`;
  }
  const record = row as Record<string, unknown>;
  const takes = (candidate: Record<string, unknown>) =>
    refusalOf(reader, [candidate], "a probe") === null;
  const culprits: string[] = [];
  for (const key of Object.keys(record)) {
    const { [key]: _dropped, ...without } = record;
    if (!takes(without)) continue;
    const channels = record.channels;
    if (key === "channels" && Array.isArray(channels)) {
      const named = channels.flatMap((channel, i) =>
        takes({ ...record, channels: channels.filter((_, j) => j !== i) })
          ? [`channels[${i}] ${quoted(channel)}`]
          : [],
      );
      culprits.push(...(named.length > 0 ? named : [`"channels" ${quoted(channels)}`]));
    } else {
      culprits.push(`"${key}" ${quoted(record[key])}`);
    }
  }
  return culprits.length > 0
    ? `it takes the row without ${culprits.join(", or without ")}`
    : `no single field explains it; the reader says: ${words}`;
}

/** Reads `document` (the parsed index.json) with `reader`, as a server reads a remote index. */
export function judge(document: unknown, source: string, reader: Reader): Verdict {
  const message = refusalOf(reader, document, source);
  if (message === null) return { reader: reader.label };
  const rows: RowRefusal[] = [];
  if (Array.isArray(document)) {
    document.forEach((row, index) => {
      const words = refusalOf(reader, [row], `${source}, row ${index} alone`);
      if (words !== null) {
        rows.push({ index, row: rowName(row), reason: reasonFor(reader, row, words) });
      }
    });
  }
  return { reader: reader.label, refusal: { message, rows } };
}
