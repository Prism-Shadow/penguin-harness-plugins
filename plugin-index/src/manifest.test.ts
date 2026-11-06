import { describe, expect, it } from "vitest";
import { ManifestError, manifestPathFor, parseManifest } from "./manifest.js";

// sha512 of 64 bytes of 0x01, npm base64 form; digest starts 0101….
const INTEGRITY = `sha512-${Buffer.from("01".repeat(64), "hex").toString("base64")}`;
// The same digest in base64url — the entry key on a keyed path.
const KEY =
  "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ";

const FIELDS = `
name = "@acme/widgets"
version = "1.2.3"
description = "Widget tools for PenguinHarness."
authors = ["Ada <@ada>"]
license = "MIT"
repository = "https://github.com/acme/widgets"
keywords = ["widgets", "linux"]
categories = ["software-development"]
owners = ["ada"]
integrity = "${INTEGRITY}"
`;

const CHANNELS = `
[[channels]]
kind = "npm"
`;

const VALID = FIELDS + CHANNELS;

const FILE = "packages/@acme/wi/dg/widgets/1.2.3/manifest.toml";
const KEYED_FILE = `packages/@acme/wi/dg/widgets/1.2.3/${KEY}/manifest.toml`;

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
    expect(manifest.categories).toEqual(["software-development"]);
    expect(manifest.integrity).toBe(INTEGRITY);
    expect(manifest.yanked).toBeUndefined();
  });

  it("requires the path to spell the name", () => {
    expect(problemsOf(VALID, `packages/wi/dg/widgets/1.2.3/${KEY}/manifest.toml`)).toContainEqual(
      expect.stringContaining("must live under packages/@acme/wi/dg/widgets/"),
    );
  });

  it("requires the path to spell the bucket", () => {
    expect(problemsOf(VALID, `packages/@acme/zz/zz/widgets/1.2.3/${KEY}/manifest.toml`)).toEqual([
      expect.stringContaining("under bucket zz/zz, its name spells wi/dg"),
    ]);
  });

  it("requires the path to spell the version", () => {
    expect(problemsOf(VALID, `packages/@acme/wi/dg/widgets/1.2.4/${KEY}/manifest.toml`)).toEqual([
      expect.stringContaining("the path says version 1.2.4, the manifest says 1.2.3"),
    ]);
    expect(problemsOf(VALID, "packages/@acme/wi/dg/widgets/1.2.4/manifest.toml")).toEqual([
      expect.stringContaining("the path says version 1.2.4, the manifest says 1.2.3"),
    ]);
  });

  it("requires a keyed path's key to spell the integrity", () => {
    const wrong = KEY.slice(0, -1) + "B";
    expect(problemsOf(VALID, `packages/@acme/wi/dg/widgets/1.2.3/${wrong}/manifest.toml`)).toEqual([
      expect.stringContaining(`is not "integrity"'s digest in base64url (${KEY})`),
    ]);
    expect(problemsOf(VALID, KEYED_FILE)).toEqual([]);
  });

  it("accepts a keyless path — the ordinary entry has no key segment", () => {
    expect(problemsOf(VALID)).toEqual([]);
  });

  it("requires the manifest directly in the version directory, or one key below it", () => {
    expect(problemsOf(VALID, "packages/@acme/wi/dg/widgets/1.2.3")).toContainEqual(
      expect.stringContaining(
        "must be packages/@acme/wi/dg/widgets/<version>/manifest.toml or packages/@acme/wi/dg/widgets/<version>/<key>/manifest.toml",
      ),
    );
    expect(
      problemsOf(VALID, `packages/@acme/wi/dg/widgets/1.2.3/${KEY}/manifest.toml/extra`),
    ).toContainEqual(expect.stringContaining("or packages/@acme/wi/dg/widgets/<version>/<key>/"));
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
categories = ["nonsense", "software-development", "software-development"]
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

  it("takes an optional card title in either language", () => {
    const titled = parseManifest(
      FIELDS + `title = "Widget Language Support"\ntitle_zh = "组件语言支持"\n` + CHANNELS,
      FILE,
    );
    expect(titled.title).toBe("Widget Language Support");
    expect(titled.title_zh).toBe("组件语言支持");
    expect(problemsOf(VALID)).toEqual([]);
    expect(problemsOf(FIELDS + `title = ""\n` + CHANNELS)).toContainEqual(
      expect.stringContaining('"title" must be a non-empty string'),
    );
    expect(problemsOf(FIELDS + `title_zh = "   "\n` + CHANNELS)).toContainEqual(
      expect.stringContaining('"title_zh" must be a non-empty string'),
    );
    expect(problemsOf(FIELDS + `title = 3\n` + CHANNELS)).toContainEqual(
      expect.stringContaining('"title" must be a non-empty string'),
    );
    expect(problemsOf(FIELDS + `title = "${"x".repeat(1001)}"\n` + CHANNELS)).toContainEqual(
      expect.stringContaining('"title" is longer than 1000 characters'),
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
    expect(manifestPathFor("@acme/widgets", "1.2.3")).toBe(FILE);
    expect(manifestPathFor("solo", "0.1.0")).toBe("packages/so/lo/solo/0.1.0/manifest.toml");
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
      problemsOf(`${VALID}\n[[channels]]\nkind = "mirror"\nurl = "http://x/y.tgz"\n`),
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
