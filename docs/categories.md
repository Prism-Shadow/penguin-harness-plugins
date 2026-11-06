# Categories

Every plugin declares one to three categories. The list mirrors the harness's own
`PLUGIN_CATEGORIES` one for one — same ids, same order, same intent — so a card filed
here is grouped by the plugin page under the same heading its category names, and a
reader can tell from the catalogue who a plugin serves, not merely what it does.

The list is closed. Widening it is a normal pull request against
`plugin-index/src/vocabulary.ts` and this file, and a good reason is a plugin that genuinely does
not fit.

| Category               | For plugins that …                                                                            |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `rsi`                  | serve agent self-evolution — the RSI toolkits: the default loop and one plugin per algorithm. |
| `office-productivity`  | serve office work: documents, slides, data, the company mode and its kin.                     |
| `software-development` | serve people writing code: engineering practice, syntax highlighting, build and test tooling. |
| `ai-app-development`   | serve building AI apps: agents, prompts, model plumbing.                                      |
| `sandbox`              | provide a sandbox backend — the confinement applied to spawned processes.                     |

Pick the floor your plugin _provides on_, not the one it consumes. A tool that happens to
run its work in a sandbox is a `tool`.
