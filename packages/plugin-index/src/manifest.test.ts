import { describe, expect, it } from "vitest";
import { ManifestError, manifestPathFor, parseManifest } from "./manifest.js";

const INTEGRITY = `sha256-${"0123456789abcdef".repeat(4)}`;

const VALID = `
name = "@acme/widgets"
version = "1.2.3"
description = "Widget tools for PenguinHarness."
authors = ["Ada <@ada>"]
license = "MIT"
repository = "https://github.com/acme/widgets"
keywords = ["widgets", "linux"]
categories = ["tool"]
owners = ["ada"]
integrity = "${INTEGRITY}"
`;

const FILE = "plugins/@acme/widgets/1.2.3/0123456789abcdef/manifest.toml";

function problemsOf(source: string, file = FILE): string[] {
  try {
    parseManifest(source, file);
    return [];
  } catch (err) {
    expect(err).toBeInstanceOf(ManifestError);
    return (err as ManifestError).problems;
  }
}

describe("parseManifest", () => {
  it("accepts a complete manifest", () => {
    const manifest = parseManifest(VALID, FILE);
    expect(manifest.name).toBe("@acme/widgets");
    expect(manifest.categories).toEqual(["tool"]);
    expect(manifest.integrity).toBe(INTEGRITY);
    expect(manifest.yanked).toBeUndefined();
  });

  it("requires the path to spell the name", () => {
    expect(
      problemsOf(VALID, "plugins/widgets/1.2.3/0123456789abcdef/manifest.toml"),
    ).toContainEqual(expect.stringContaining("must live under plugins/@acme/widgets/"));
  });

  it("requires the path to spell the version", () => {
    expect(problemsOf(VALID, "plugins/@acme/widgets/1.2.4/0123456789abcdef/manifest.toml")).toEqual(
      [expect.stringContaining("the path says version 1.2.4, the manifest says 1.2.3")],
    );
  });

  it("requires the path to spell the first 16 hex digits of the integrity", () => {
    expect(problemsOf(VALID, "plugins/@acme/widgets/1.2.3/0123456789abcdee/manifest.toml")).toEqual(
      [expect.stringContaining("is not the first 16 hex digits")],
    );
  });

  it("requires an entry directory between the version and the manifest", () => {
    expect(problemsOf(VALID, "plugins/@acme/widgets/1.2.3/manifest.toml")).toContainEqual(
      expect.stringContaining("must be plugins/@acme/widgets/<version>/<key>/manifest.toml"),
    );
  });

  it("requires a well-formed integrity", () => {
    const without = VALID.replace(/^integrity = .*$/m, "");
    expect(problemsOf(without)).toContainEqual(expect.stringContaining('"integrity" is required'));
    const sha512 = VALID.replace(/^integrity = .*$/m, `integrity = "sha512-abc"`);
    expect(problemsOf(sha512)).toContainEqual(expect.stringContaining("64 lowercase hex digits"));
  });

  it("reports every problem at once", () => {
    const problems = problemsOf(`
name = "@acme/widgets"
version = "^1.0.0"
description = ""
authors = ["Ada <not-an-email>"]
license = "Commercial"
repository = "http://example.com"
categories = ["nonsense", "tool", "tool"]
owners = []
integrity = "${INTEGRITY}"
surprise = 1
`);
    expect(problems).toEqual(
      expect.arrayContaining([
        expect.stringContaining('unknown key "surprise"'),
        expect.stringContaining("not an exact semantic version"),
        expect.stringContaining('"description" must not be empty'),
        expect.stringContaining("neither a GitHub handle"),
        expect.stringContaining("not in this index's allowed list"),
        expect.stringContaining('"repository" must be an https URL'),
        expect.stringContaining('unknown category "nonsense"'),
        expect.stringContaining("must not repeat a category"),
        expect.stringContaining('"owners" is required'),
      ]),
    );
  });

  it("ties yanked to a reason in both directions", () => {
    expect(problemsOf(`${VALID}\nyanked = true\n`)).toContainEqual(
      expect.stringContaining("must say why"),
    );
    expect(problemsOf(`${VALID}\nyanked_reason = "gone"\n`)).toContainEqual(
      expect.stringContaining("only allowed together with yanked = true"),
    );
    expect(parseManifest(`${VALID}\nyanked = true\nyanked_reason = "gone"\n`, FILE).yanked).toBe(
      true,
    );
  });

  it("names the one place an entry's manifest may live", () => {
    expect(manifestPathFor("@acme/widgets", "1.2.3", INTEGRITY)).toBe(FILE);
    expect(manifestPathFor("solo", "0.1.0", INTEGRITY)).toBe(
      "plugins/solo/0.1.0/0123456789abcdef/manifest.toml",
    );
  });
});
