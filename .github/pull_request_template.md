<!--
Submitting a plugin? Keep this template and fill it in. One plugin per pull request,
and please title the PR `<package name>@<version>`.

Changing the tooling or docs instead? Delete all of this and write a plain description.
-->

I am submitting

- [ ] a new plugin
- [ ] an update to a plugin I own
- [ ] a yank

**What it does and who it is for:**
<!-- A couple of sentences. For an update, say what changed. -->

**What it takes:**
<!-- Does it spawn processes, read outside the project root, or reach the network? Say so. -->

For a new plugin, I confirm that I have:

- [ ] published the package on npm, with `"type": "module"` and a named `activate` export
- [ ] used a name starting with `penguin-plugin-` that names my implementation rather
      than claiming a whole category
- [ ] chosen an [allowed license](../docs/licensing.md) and used the same identifier in
      `package.json`, in the manifest, and in my repository's license file
- [ ] listed myself in `owners`
- [ ] tested the plugin in a real deployment via `plugins.json`
- [ ] read [CONTRIBUTING.md](../CONTRIBUTING.md)
