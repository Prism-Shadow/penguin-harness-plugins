# Categories

Every plugin declares one to three categories. They mirror the harness's own capability
stack, so a reader can tell from the catalogue which part of the system a plugin plugs
into — not merely what topic it belongs to.

The list is closed. Widening it is a normal pull request against
`src/lib/vocabulary.ts` and this file, and a good reason is a plugin that genuinely does
not fit.

| Category      | For plugins that …                                                                                          |
| ------------- | ----------------------------------------------------------------------------------------------------------- |
| `sandbox`     | provide a sandbox backend — the confinement applied to spawned processes.                                   |
| `tool`        | provide tools an agent can call.                                                                            |
| `workflow`    | provide workflows: named, agent-shaped units the harness can run.                                           |
| `terminal`    | extend the terminal and shell surface.                                                                      |
| `integration` | connect the harness to an external service or another program.                                              |
| `utility`     | do something useful that none of the above describes. Use it when nothing else fits, not as a second label. |

Pick the floor your plugin _provides on_, not the one it consumes. A tool that happens to
run its work in a sandbox is a `tool`.
