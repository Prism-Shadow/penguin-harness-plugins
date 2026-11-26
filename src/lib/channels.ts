/**
 * Where an entry's channels serve its tarball, and how index.json spells them.
 *
 * A manifest's npm channel names no address: the registry serves every version of every
 * package at one standard place, `<registry>/<name>/-/<name without its scope>-<version>.tgz`,
 * which is the `dist.tarball` the registry itself reports (check-npm compares the two). Writing
 * it into a manifest would only be a second spelling of the entry's name and version, one that
 * could disagree with them.
 *
 * The published document spells it out all the same: there, every channel carries a `url`.
 * PenguinHarness 0.2.19 refuses the WHOLE index when a single channel has none (see
 * harness-readers.ts), and a reader that installs from npm by name has no use for the address
 * and ignores it. So the build writes the registry's address of exactly that name and version
 * into the npm channel, and every other channel is published as its manifest lists it.
 */
import type { Channel, Manifest } from "@prismshadow/penguin-plugin-index";

/** The registry an `npm` channel names. */
export const NPM_REGISTRY = "https://registry.npmjs.org";

/** A channel as index.json publishes it: always with an address. */
export interface PublishedChannel {
  kind: string;
  url: string;
}

/**
 * The registry's tarball address for `name@version`: `@acme/widgets` 1.2.3 is
 * `https://registry.npmjs.org/@acme/widgets/-/widgets-1.2.3.tgz`, `left-pad` 1.3.0 is
 * `https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz`.
 */
export function npmTarballUrl(name: string, version: string): string {
  const file = name.slice(name.indexOf("/") + 1);
  return `${NPM_REGISTRY}/${name}/-/${file}-${version}.tgz`;
}

/** The entry's channels in the manifest's order, the npm one at the registry's address. */
export function publishedChannels(
  manifest: Pick<Manifest, "name" | "version" | "channels">,
): PublishedChannel[] {
  // parseManifest guarantees the shape: the npm channel has no url, every other one has one.
  return manifest.channels.map((channel: Channel) =>
    channel.kind === "npm"
      ? { kind: channel.kind, url: npmTarballUrl(manifest.name, manifest.version) }
      : { kind: channel.kind, url: channel.url as string },
  );
}
