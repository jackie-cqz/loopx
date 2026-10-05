# ZCode goal mode

LoopX adapter for [ZCode](https://zcode.z.ai/) — a terminal coding agent
supporting [skills](https://zcode.z.ai/en/docs/skill), [Goal Mode](https://zcode.z.ai/en/docs/goal),
and [Automations](https://zcode.z.ai/en/docs/automations).

## What this surface is

ZCode discovers user skills from `~/.zcode/skills/<skill-name>/SKILL.md`.
While ZCode provides native Goal Mode and Automations, LoopX currently
integrates through the managed `$loopx` skill facade. In this mode, the loop
driver is the agent's own turn loop gated by LoopX quota — every continuation
enters through `quota should-run`, and a stop decision ends the session loop.

Direct machine binding to ZCode native Goal Mode or Automations is not yet
integrated and will be supported through dedicated provider contracts in the
future.

## Install

```bash
loopx slash-commands --install --surface zcode
```

Writes the managed LoopX skill facades (`loopx`, `loopx-global-*`, …) into
`ZCODE_HOME/skills` (default `~/.zcode/skills`; override with `ZCODE_HOME`).
Managed files carry the `loopx-managed-slash-command` marker and are refreshed by
rerunning the installer; user-owned files are never overwritten.

After installation, refresh or read back installed skills in ZCode via Settings → Skills.

## Diagnose this host

```bash
loopx doctor --agent-type zcode
```

The ZCode-specific readback distinguishes a CLI found on `PATH`, an installed
Desktop application and its bundled CLI, and an explicitly selected source
checkout. Use these overrides when an installation is outside the usual
discovery locations:

```bash
loopx doctor --agent-type zcode --zcode-cli /path/to/zcode
loopx doctor --agent-type zcode --zcode-desktop /path/to/ZCode
loopx doctor --agent-type zcode --zcode-source /path/to/ZCode-checkout
```

These options require `--agent-type zcode` and can be combined. A source
checkout's package version is reported separately from the version of its
existing runnable `dist` build; a checkout does not establish that either host
is installed. Desktop metadata is also separate from its bundled CLI probe.

The probes use only version/help commands, with temporary `ZCODE_HOME` and
`ZCODE_STORAGE_DIR` and `ZCODE_DATA_BASE_DIR` directories. They do not launch Desktop, run a model, or
perform an `app-server` protocol handshake. Help output establishes advertised
commands and options; it does not verify session execution, native Goal Mode,
Automations, authentication, or model availability. LoopX's integrated boundary
remains the managed skill facade.

Skill readback uses ZCode's own `ZCODE_HOME/skills`, including the legacy
`ZCODE_AGENTS_HOME` fallback when `ZCODE_HOME` is unset. It checks the generated
LoopX facade set rather than the Codex workflow-skill directory, and reports
missing, outdated, user-owned, or unreadable files. These checks are read-only;
runtime skill loading remains unverified. The existing doctor exit-status
contract is unchanged: required LoopX installation/runtime checks determine
the overall result. Inspect `skill_delivery.status` and `zcode` host
observations for integration readiness even when the command exits zero.

Repair managed files with:

```bash
loopx slash-commands --install --surface zcode
```

For a custom LoopX executable, install with
`loopx slash-commands --install --surface zcode --cli-bin /path/to/loopx`.
Doctor accepts a consistent custom command in the current generated template;
no additional doctor option is needed. Use the same `--cli-bin` when repairing.
The installer preserves user-owned files; resolve a reported path conflict
before expecting that facade to become available.

This is a maintenance readback within the existing doctor and host-adapter
boundary (roadmap S4/S8/S12), not a new capability or a native execution
provider. Filesystem and subprocess observations stay in the existing Python
doctor/provider adapter; they do not duplicate TypeScript control-plane state
or lifecycle decisions. Acceptance covers discovery, version and advertised
interface evidence, ZCode facade readback, and actionable failure states.
Native execution and scheduler lifecycle acceptance remain separate.

## Use

From a ZCode session in a connected project, invoke the `$loopx` skill (or type
`/loopx <complex task>`). The facade instructs the agent to run:

```bash
loopx start-goal --guided --project . --slash-command-arguments="<task>" --host-surface zcode
```

After todo writeback, carry the generated heartbeat task body as the session
objective and start every following turn with `quota should-run`.

## Layout

- `__init__.py` — host facts: install surface id, skills root resolution, and
  the env override used by the installer and the activation packet.
- `diagnostics.py` — isolated CLI metadata probes and Desktop/source discovery.
