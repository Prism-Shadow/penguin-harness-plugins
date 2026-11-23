# Historical date-count reports

The historical day-difference experiment reports in this directory used an
incorrect oracle for dates exclusive of both endpoints. They also reused a
held-out task as a deep-training target. The original artifacts are preserved
for audit, but their baseline, evolved scores, and claimed improvements are
invalid and must not be used as evidence. The scripts and oracle were corrected;
a new independent run is required. No GDPevo results are claimed here.

The historical modular-exponentiation report reused a test task as a development target; it is not a valid held-out comparison. Its unused reference table was inaccurate; the container oracle itself computed powers directly. Original artifacts are preserved.

## GDPevo service-ticket pilot (one repetition)

- Benchmark checkout: `010fda6b775f864814188cde54efb8c927b4c6b9`;
  model `custom/deepseek-v4.1-flash`, thinking low, PenguinHarness 0.2.18.
- Training: five questions in one continuous session; reward 0.9411764.
  This run used the earlier Skill-directed archive, not the later MCP bridge or
  the new standalone protocol entry point.
- Test, five held-out questions in order 001–005:
  baseline 0.823529, 0.166667, 0.823529, 0.545455, 0.285714
  (mean 0.5289788);
  frozen Skill 0.882353, 0.555556, 0.823529, 0.727273, 0.571429
  (mean 0.7120280). Both jobs completed 5/5 with no reported errors.
- Baseline State archive SHA-256:
  `984d7ed4a62df43775e73f8fd0d84c1f22d51d2c162307bd3487736df26c98cf`.
  Frozen State archive SHA-256:
  `577ea6fb548db1e90e37bef2299dd64e586bd3d8ed7e86486a7c851ecb12825a`.
  Frozen Skill SHA-256 at archive creation:
  `92f29b3912b44c8285c0bb8f21a526a0ff8ce4a76c849b4624c92d9ad9a4db11`.
- One fixed scenario and one repetition do not establish statistical stability.
  The test artifacts do not preserve a complete post-run Agent State, so they
  do not prove that the mounted Skill was unchanged or OS-write-protected during
  each test. Separate subagent Sessions shared the benchmark filesystem.
- On GPU02, authoritative job results are under
  `/home/yichen/rsiagent-penguin/experiments/gdpevo-service-rsi-v1/jobs/`:
  `rsiagent-service-train-v5`, `rsiagent-baseline-test-v1`, and
  `rsiagent-frozen-test-v1`.

## Hosted protocol smoke (synthetic model, no benchmark score)

On GPU02, an isolated Penguin host with bwrap read-only Sessions and a masked
Penguin data root ran the current broad/deep training path against the real
GDPevo service-ticket training API and train-only judge. A deterministic local
model selected train_001, queried the catalog, returned an intentionally empty
answer, and produced two reconciled lessons. The target status was STALLED
because the practice budget was zero; this is not a successful task outcome.
The report records Curriculum 3, Actor 9, and Verifier 2 calls. The frozen
snapshot digest was
`cd9cafc66fa7d806e929385c9b1ecbd3b39b9462f9e6b4ec16b0fcf41d1e76d6`.
This validates integration with synthetic responses, not model performance,
host-side write protection, or the original VM-based isolation and recovery.
Artifacts: `/home/yichen/rsiagent-penguin/experiments/rsi-hosted-protocol-retry-7hi7pt8f/`.

## Real-model training on the GDPevo train environment (current implementation)

- Environment: the task group's own support-console API started locally
  (`TASKS/env/setup.sh`, `127.0.0.1:9003`), with the train-only judge enabled
  (`TASK_ENV_ENABLE_JUDGE=1`). Model `deepseek-v4.1-flash` through the
  OpenAI-compatible endpoint; entry point `runtime/cli.py train`
  (`--host chat`, `--environment-max-requests 16`).
- The run completed and wrote a frozen snapshot:
  `snapshot.json` digest
  `6456636d376a21c49682f045dae53e8342b2ccac4a1bce51cf3b80ac676b4e0c`,
  1 structured lesson, mode 0600.
- Role calls: Curriculum 6, Actor 54, Verifier 46. `target_status: STALLED`,
  `broad_status: WAVE_LIMIT_REACHED`, one wave.
- What this establishes: the current implementation drives real roles against
  the real GDPevo training API and its train-only judge, and produces a
  loadable, digest-checked snapshot. What it does not establish: a solved
  target (the run stalled), benchmark performance (the Verifier here is model
  judgment with train-judge feedback, not the benchmark's container verifier),
  or host isolation (`--host chat`; no masking configured).
- Artifacts: `/home/yichen/rsiagent-penguin/experiments/real-model-train-current/`
  (`input.json`, `report.json`, `snapshot.json`).

## Current Penguin protocol: service-ticket exploratory run

On GPU02, the working tree based on commit `7dcefd2` ran the modified CLI
against `deepseek-v4.1-flash`. Training used five public training tasks, one
two-attempt wave, two wave workers, legacy deep mode and one practice. It
completed with `BUDGET_EXHAUSTED`, three lessons and role calls Curriculum
3, Actor 84, Verifier 70. Frozen snapshot SHA-256:
`30d484f4c23179fca50b407e017dff9b9544770acf400d9c8882939894151edb`.

The five held-out test questions scored by their original graders were
0.764706, 0.0, 0.823529, 0.363636 and 0.285714 (mean 0.447517).
The second task initially failed with no usable Penguin session result and
was retried once; the retry and subsequent tasks used a 300-second model
timeout. Digest checks passed in every successful evaluation.

This is an exploratory single-scenario run, not a formal isolated benchmark:
Penguin sessions were not verified read-only or filesystem-isolated, and test
questions shared a separately started, judge-disabled service instead of fresh
per-task containers. No matched baseline was run. It does not prove snapshot
write protection, privacy isolation, or improvement over baseline. The model
returned a valid frozen snapshot despite the training target remaining unsolved.
Artifacts: `/home/yichen/rsiagent-penguin/experiments/rsi-real-current-2ncdqxbz/`.

## GPU02 protected-session boundary checks

A dedicated Penguin host was configured with the RSIAgent bwrap namespace
wrapper. A synthetic-model `evaluate` invocation completed with separate
Actor and Verifier Sessions and the frozen digest unchanged. Session creation
and saved policy inspection confirmed read-only mode, no network, and masks
covering the host data root and a frozen snapshot.

A direct synthetic bwrap command using that wrapper and corresponding masks
could not read Actor-private data or the snapshot, overwrite or replace the
snapshot, inspect another process's command line or open its file descriptor,
or connect to a host loopback listener. The snapshot's post-run hash matched.
A snapshot with a second hard link was rejected by the protected CLI before
evaluation. These are focused probes, not proof against every host tool or
host-side writer. Before adding the namespace wrapper the same synthetic
command-line probe could read another process's command line.

The 0.447517 GDPevo mean above predates these isolation changes and was
obtained without the protected path. No new real-model protected benchmark
run or matched baseline is claimed.
