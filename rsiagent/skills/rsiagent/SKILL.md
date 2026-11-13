---
name: rsiagent
description: Explore an environment with curriculum, actor and verifier roles and reuse frozen memory.
---

# RSIAgent

A skill-directed adaptation of https://arxiv.org/abs/2609.15364 and https://github.com/AetherLabsAI/RSIAgent, not the original runtime. Report deviations; do not claim paper results without evidence.

1. Establish an authorized environment, training tasks, separate held-out tasks, and an exploration budget. Record model, environment, split and budget. Do not use held-out tasks or scores for exploration.
2. Broad phase: Curriculum proposes diverse training tasks. Actor executes from a shared starting memory snapshot. Use isolated reset environments when available; if the benchmark requires one continuous environment, preserve it, run branches sequentially, and disclose this departure from the paper. Verifier independently checks requirements against environment evidence without access to Actor private reasoning or memory. After each wave, Actor consolidates verified successes and failures in a deterministic order, recording evidence, conditions and uncertainty.
3. Deep phase: Curriculum selects focused practice based on failures and fragile successes. Actor executes under the benchmark's environment rules; Verifier checks the outcome independently without access to Actor-private memory. Consolidate grounded lessons before the next attempt, within budget.
4. Freeze a versioned memory snapshot. On held-out tasks, Actor reads only that snapshot; disable Curriculum and memory updates. Verifier checks execution independently. Keep benchmark scores outside the learning loop. Compare with a matched baseline and report results and limitations.

If the host cannot isolate roles or enforce read-only memory, explicitly report the limitation; do not claim those properties were verified.

## PenguinHarness execution

For a standalone text task, when `mcp__rsiagent__rsi_train` is available,
use that tool to execute training through the protocol core; provide the Project,
workspace, provider/model, three distinct existing Agent IDs, input and new
snapshot/report paths. The MCP tools default to host-reported read-only
Sessions and require host masks covering `PENGUIN_HOME` and the training
snapshot's parent directory, or the existing snapshot during evaluation. They
stop if confinement or the required masks cannot be
confirmed; do not silently disable those requirements. Report `target_status`,
`broad_status` and `waves_completed`; a created snapshot does not imply that the
target passed. For held-out text tasks use
`mcp__rsiagent__rsi_evaluate` with the trusted training digest and the same
snapshot. These tools use separate role Sessions but do not enforce private-file
isolation or host-level write protection. They do not operate GDPevo services;
use the benchmark-authorized workflow for environment tasks and disclose the
difference. Never pass credentials in tool arguments.

Run as a top-level session with the `run_subagent` tool available. Use fresh,
separate subagent sessions for Curriculum and Verifier; give each only the inputs
specified for that role. Actor works in the benchmark-authorized environment.
Maintain a controller-owned experiment record with the chosen budget, training
tasks, each verifier verdict, memory revisions, snapshot digests and deviations.

For each training wave, freeze a copy of the current memory before choosing or
executing projects. Curriculum selects varied practice projects; Actor attempts
each with that same starting snapshot. Verifier receives the task and observable
candidate evidence, not Actor memory or private reasoning. Collect all verifier
verdicts before Actor consolidates supported lessons in project order. An
unresolved verification blocks that wave from committing memory. Each lesson
should state conditions, action, observable outcome, and supporting evidence;
keep failures and uncertainty rather than inventing a causal rule.

In deep exploration, attempt the unchanged development target first. Keep one
target-scoped Curriculum session across routing and practice. A Verifier FAIL
goes to Curriculum: REVISE returns the complete verifier report to the same
Actor and keeps the current attempt; EVOLVE lets that Actor distill the verified
failure, suspends the attempt, and starts focused practice. During practice,
Curriculum may select multiple projects before READY_FOR_RETRY; only then start
a fresh attempt at the unchanged target. STALLED and budget exhaustion are
reported separately from success.

After a Verifier PASS, Curriculum may choose HANDOFF or VERIFY_MORE. VERIFY_MORE
returns the review report to the same Verifier and rechecks the same candidate;
it does not change the candidate or turn a PASS into a FAIL by itself. Only the
Verifier's new verdict can do that. Treat an incomplete verification or missing
report as unverified, not a PASS. Keep role histories separate and do not send
Actor-private memory or reasoning to Verifier. The host must enforce the stated
isolation rather than relying on these instructions.

A fresh subagent conversation alone does not isolate shared files or processes.
If the host cannot hide Actor-private artifacts and prevent verifier modifications
from affecting the candidate, stop before claiming independent verification and
report the limitation. The Skill instructions do not enforce those boundaries.

## Continuous-training benchmark adaptation

When a benchmark explicitly requires a single continuous training session and
environment, its run rules take precedence over independent-environment resets.
Keep that training environment and session intact; separate the training and
held-out tasks. Use no held-out feedback during exploration. Record that shared
environment state and sequential branches differ from the paper's independent VMs.
Do not pretend that separate prompts provide operating-system isolation.

Export a nonempty reusable Skill and the benchmark-required answers; keep private
training transcripts and verifier material out of the exported Skill. Freeze and
record a digest of the Skill before evaluation. For each held-out case, use the
benchmark's fresh environment and the same frozen Skill; do not update it during
testing. Compare a no-Skill baseline with the evolved Skill under matched model,
tools, cases, and budgets. Report training and test scores separately.
