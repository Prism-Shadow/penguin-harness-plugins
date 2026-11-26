/**
 * The channels index.json publishes for an entry.
 *
 * - Every published channel carries a `url`: a harness reader that finds one without refuses the
 *   whole document.
 * - The npm channel's is the registry's tarball address of exactly the entry's name and version
 *   (the registry's own `dist.tarball`), scoped or not.
 * - Every other channel is published as its manifest lists it, in the manifest's order.
 */
import { describe, expect, it } from "vitest";
import { npmTarballUrl, publishedChannels } from "./channels.js";

const GITHUB = {
  kind: "github",
  url: "https://github.com/acme/widgets/releases/download/v1.2.3/widgets-1.2.3.tgz",
};

describe("publishedChannels", () => {
  it("publishes the npm channel at the registry's tarball address of that exact version", () => {
    expect(
      publishedChannels({
        name: "@acme/widgets",
        version: "1.2.3-rc.1",
        channels: [{ kind: "npm" }],
      }),
    ).toEqual([
      { kind: "npm", url: "https://registry.npmjs.org/@acme/widgets/-/widgets-1.2.3-rc.1.tgz" },
    ]);
  });

  it("spells an unscoped package's address without a scope", () => {
    expect(npmTarballUrl("left-pad", "1.3.0")).toBe(
      "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
    );
  });

  it("keeps every other channel as the manifest lists it, in its order", () => {
    expect(
      publishedChannels({
        name: "@acme/widgets",
        version: "1.2.3",
        channels: [GITHUB, { kind: "npm" }],
      }),
    ).toEqual([
      GITHUB,
      { kind: "npm", url: "https://registry.npmjs.org/@acme/widgets/-/widgets-1.2.3.tgz" },
    ]);
    expect(
      publishedChannels({ name: "@acme/widgets", version: "1.2.3", channels: [GITHUB] }),
    ).toEqual([GITHUB]);
  });
});
