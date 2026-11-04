import { describe, expect, it } from "vitest";
import { ManifestError, manifestPathFor, parseManifest } from "./manifest.js";

// sha512 of 64 bytes of 0x01, npm base64 form; digest starts 0101….
const INTEGRITY = `sha512-${Buffer.from("01".repeat(64), "hex").toString("base64")}`;

const FIELDS = `
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

const CHANNELS = `
[[channels]]
kind = "npm"
`;

const VALID = FIELDS + CHANNELS;

const FILE = "packages/@acme/wi/dg/widgets/1.2.3/0101010101010101/manifest.toml";

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
      problemsOf(VALID, "packages/wi/dg/widgets/1.2.3/0123456789abcdef/manifest.toml"),
    ).toContainEqual(expect.stringContaining("must live under packages/@acme/wi/dg/widgets/"));
  });

  it("requires the path to spell the bucket", () => {
    expect(
      problemsOf(VALID, "packages/@acme/zz/zz/widgets/1.2.3/0123456789abcdef/manifest.toml"),
    ).toEqual([expect.stringContaining("under bucket zz/zz, its name spells wi/dg")]);
  });

  it("requires the path to spell the version", () => {
    expect(
      problemsOf(VALID, "packages/@acme/wi/dg/widgets/1.2.4/0101010101010101/manifest.toml"),
    ).toEqual([expect.stringContaining("the path says version 1.2.4, the manifest says 1.2.3")]);
  });

  it("requires the path to spell the first 16 hex digits of the integrity", () => {
    expect(
      problemsOf(VALID, "packages/@acme/wi/dg/widgets/1.2.3/0123456789abcdee/manifest.toml"),
    ).toEqual([expect.stringContaining("is not the first 16 hex digits")]);
  });

  it("requires an entry directory between the version and the manifest", () => {
    expect(problemsOf(VALID, "packages/@acme/wi/dg/widgets/1.2.3/manifest.toml")).toContainEqual(
      expect.stringContaining("must be packages/@acme/wi/dg/widgets/<version>/<key>/manifest.toml"),
    );
  });

  it("requires a well-formed integrity", () => {
    const without = VALID.replace(/^integrity = .*$/m, "");
    expect(problemsOf(without)).toContainEqual(expect.stringContaining('"integrity" is required'));
    const sha512 = VALID.replace(/^integrity = .*$/m, `integrity = "sha512-abc"`);
    expect(problemsOf(sha512)).toContainEqual(expect.stringContaining("88 base64 characters"));
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
    expect(problemsOf(FIELDS + `yanked = true\n` + CHANNELS)).toContainEqual(
      expect.stringContaining("must say why"),
    );
    expect(problemsOf(FIELDS + `yanked_reason = "gone"\n` + CHANNELS)).toContainEqual(
      expect.stringContaining("only allowed together with yanked = true"),
    );
    expect(
      parseManifest(FIELDS + `yanked = true\nyanked_reason = "gone"\n` + CHANNELS, FILE).yanked,
    ).toBe(true);
  });

  it("names the one place an entry's manifest may live", () => {
    expect(manifestPathFor("@acme/widgets", "1.2.3", INTEGRITY)).toBe(FILE);
    expect(manifestPathFor("solo", "0.1.0", INTEGRITY)).toBe(
      "packages/so/lo/solo/0.1.0/0101010101010101/manifest.toml",
    );
  });
});

describe("parseManifest: channels", () => {
  const GITHUB = `
[[channels]]
kind = "github"
url = "https://github.com/acme/widgets/releases/download/v1.2.3/widgets-1.2.3.tgz"
`;

  it("accepts an npm-only entry and carries its channel", () => {
    const manifest = parseManifest(VALID, FILE);
    expect(manifest.channels).toEqual([{ kind: "npm" }]);
  });

  it("accepts url channels beside the npm channel", () => {
    const manifest = parseManifest(`${VALID}${GITHUB}`, FILE);
    expect(manifest.channels).toEqual([
      { kind: "npm" },
      {
        kind: "github",
        url: "https://github.com/acme/widgets/releases/download/v1.2.3/widgets-1.2.3.tgz",
      },
    ]);
  });

  it("requires at least one channel", () => {
    expect(problemsOf(VALID.replace(CHANNELS, ""))).toContainEqual(
      expect.stringContaining('"channels" is required'),
    );
    expect(problemsOf(FIELDS + `channels = []\n`)).toContainEqual(
      expect.stringContaining("at least one [[channels]] table"),
    );
  });

  it("rejects a repeated npm channel and repeated kinds", () => {
    expect(problemsOf(`${VALID}\n[[channels]]\nkind = "npm"\n`)).toContainEqual(
      expect.stringContaining('kind "npm" repeats'),
    );
    expect(problemsOf(`${VALID}${GITHUB}${GITHUB}`)).toContainEqual(
      expect.stringContaining('kind "github" repeats an earlier channel'),
    );
  });

  it("rejects a url on the npm channel and its absence elsewhere", () => {
    expect(
      problemsOf(`${VALID}\n[[channels]]\nkind = "npm"\nurl = "https://x/y.tgz"\n`),
    ).toContainEqual(expect.stringContaining('url must be omitted for the "npm" channel'));
    expect(problemsOf(`${VALID}\n[[channels]]\nkind = "mirror"\n`)).toContainEqual(
      expect.stringContaining("channels[1].url must be an https URL"),
    );
  });

  it("rejects a channel-level integrity and an http URL", () => {
    expect(
      problemsOf(
        `${VALID}\n[[channels]]\nkind = "mirror"\nurl = "http://x/y.tgz"\n`,
      ),
    ).toContainEqual(expect.stringContaining("channels[1].url must be an https URL"));
    expect(
      problemsOf(
        `${VALID}\n[[channels]]\nkind = "mirror"\nurl = "https://x/y.tgz"\nintegrity = "${INTEGRITY}"\n`,
      ),
    ).toContainEqual(expect.stringContaining("channels[1].integrity is not a thing"));
  });

  it("rejects an uppercase channel kind", () => {
    expect(
      problemsOf(`${VALID}\n[[channels]]\nkind = "GitHub"\nurl = "https://x/y.tgz"\n`),
    ).toContainEqual(expect.stringContaining("channels[1].kind must be lowercase"));
  });
});
