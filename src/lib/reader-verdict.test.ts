/**
 * One harness reader's verdict on a built index.
 *
 * - A document the reader accepts passes, with nothing to report.
 * - A refused document names the reader and keeps the reader's own words.
 * - Every row the reader refuses is listed — not only the first, which is all a strict reader
 *   names — by its position and `name@version`.
 * - The reason is what the reader takes the row without: the field, or the one channel, whose
 *   removal makes the reader accept it; when no single field explains it, the reader's words.
 * - The reader is called the way a server reads a remote index.
 */
import { describe, expect, it } from "vitest";
import type { Reader } from "./load-reader.js";
import { judge } from "./reader-verdict.js";

const ROW = {
  name: "@acme/widgets",
  version: "1.2.3",
  description: "Widgets.",
  authors: ["Ada <@ada>"],
  license: "MIT",
  integrity: "sha512-AAAA",
  channels: [{ kind: "github", url: "https://example.org/widgets-1.2.3.tgz" }],
};

const NPM_WITHOUT_URL = {
  ...ROW,
  name: "@acme/gadgets",
  channels: [...ROW.channels, { kind: "npm" }],
};

/**
 * A strict reader in the shape of PenguinHarness 0.2.19's: every channel needs a string url and
 * every row a string name, and the first row it refuses fails the whole document, named only by
 * its position.
 */
function strictReader(calls: unknown[] = []): Reader {
  return {
    label: "reader@1.0.0",
    parse: (data, source, options) => {
      calls.push(options);
      if (!Array.isArray(data)) throw new Error(`plugin index from ${source} is not an array`);
      data.forEach((row: Record<string, unknown>, i) => {
        const channels = (row.channels ?? []) as Record<string, unknown>[];
        if (typeof row.name !== "string" || channels.some((c) => typeof c.url !== "string")) {
          throw new Error(`plugin index from ${source} has a malformed entry at index ${i}`);
        }
      });
      return data;
    },
  };
}

describe("judge", () => {
  it("passes a document the reader accepts", () => {
    expect(judge([ROW, ROW], "index.json", strictReader())).toEqual({ reader: "reader@1.0.0" });
  });

  it("lists every refused row by position and name@version, and the channel it refuses", () => {
    const verdict = judge(
      [ROW, NPM_WITHOUT_URL, ROW, NPM_WITHOUT_URL],
      "index.json",
      strictReader(),
    );
    expect(verdict.reader).toBe("reader@1.0.0");
    expect(verdict.refusal?.message).toBe(
      "plugin index from index.json has a malformed entry at index 1",
    );
    expect(verdict.refusal?.rows).toEqual([
      {
        index: 1,
        row: "@acme/gadgets@1.2.3",
        reason: 'it takes the row without channels[1] {"kind":"npm"}',
      },
      {
        index: 3,
        row: "@acme/gadgets@1.2.3",
        reason: 'it takes the row without channels[1] {"kind":"npm"}',
      },
    ]);
  });

  it("names a top-level field the reader takes the row without", () => {
    const verdict = judge([{ ...ROW, extra: 7 }], "index.json", {
      label: "reader@2.0.0",
      parse: (data) => {
        if ((data as Record<string, unknown>[]).some((row) => "extra" in row)) {
          throw new Error("malformed entry");
        }
        return data;
      },
    });
    expect(verdict.refusal?.rows).toEqual([
      { index: 0, row: "@acme/widgets@1.2.3", reason: 'it takes the row without "extra" 7' },
    ]);
  });

  it("keeps the reader's words when no single field explains the refusal", () => {
    const { name: _name, ...nameless } = ROW;
    const verdict = judge([nameless], "index.json", strictReader());
    expect(verdict.refusal?.rows).toEqual([
      {
        index: 0,
        row: "?@1.2.3",
        reason:
          "no single field explains it; the reader says: plugin index from index.json, row 0 alone has a malformed entry at index 0",
      },
    ]);
  });

  it("reports a document refused as a whole even when every row alone passes", () => {
    const verdict = judge({ entries: [ROW] }, "index.json", strictReader());
    expect(verdict.refusal).toEqual({
      message: "plugin index from index.json is not an array",
      rows: [],
    });
  });

  it("reads the document the way a server reads a remote index", () => {
    const calls: unknown[] = [];
    judge([ROW], "index.json", strictReader(calls));
    expect(calls).toEqual([{ icons: "drop" }]);
  });
});
