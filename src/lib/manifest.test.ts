import { describe, expect, it } from "vitest";
import { ManifestError, manifestPathFor, nameFromManifestPath, parseManifest } from "./manifest.js";

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
`;

const FILE = "extensions/@acme/widgets.toml";

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
    expect(manifest.yanked).toBeUndefined();
  });

  it("requires the file path to spell the package name", () => {
    expect(problemsOf(VALID, "extensions/widgets.toml")).toContainEqual(
      expect.stringContaining("must live at extensions/@acme/widgets.toml"),
    );
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

  it("round-trips paths and names", () => {
    expect(nameFromManifestPath(FILE)).toBe("@acme/widgets");
    expect(nameFromManifestPath("extensions/solo.toml")).toBe("solo");
    expect(nameFromManifestPath("docs/readme.md")).toBeNull();
    expect(manifestPathFor("@acme/widgets")).toBe(FILE);
  });
});
