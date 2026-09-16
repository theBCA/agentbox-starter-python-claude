# starter-app-python-claude

A small AgentBox custom application: **Python** + **claude-agent-sdk**.

It summarises a document. More usefully, it is wired so every AgentBox
control has something to actually exercise — a skill, an MCP server, a
governed package install, and an outbound call that policy decides on.

## Add it

In the admin console: **Applications → Add application**, by Git URL or ZIP
upload. Wait for *running*, then press **Try API** on its card.

## Then try these, in order

Each is one preset in the console. Roughly ten minutes end to end.

| # | Preset | What it shows | Where to look after |
|---|---|---|---|
| 1 | Health check | the app is up | — |
| 2 | Agent call | a real agent run, brokered by SecureProxy | Security → Traffic |
| 3 | Trigger DLP | a **403** — the refusal *is* the result | Security → Audit Log |
| 4 | MCP tool call | MCP Bridge, and the grant it checks | Admin → MCP |
| 5 | Trigger Defender | a flagged file is caught | Security → Audit Log |
| 6 | Install a package | TrustGate allows, blocks, or holds | Security → TrustGate |
| 7 | Fetch a URL | egress policy, refusing | the app's own card |
| 8 | Stream | the agent's turns as they happen | — |

A few of these need a word of explanation.

**Step 2 — the skill.** The summary starts with `[EXEC]`. Nothing in this
app's code does that; it comes from `skills/executive-summary-tone/`, which
AgentBox scanned, approved, and mounted read-only.

**Step 4 — expect a 502 first.** A new app has no MCP grant yet, by design.
Grant it under **Admin → MCP**: enable `<app-id>__notes-server`, validate,
approve its tool fingerprint, bind it to this app, rebuild. Then the note
saves, `/mcp/list-notes` reads it back, and `/mcp/delete-all-notes` gets
*held for approval* — it is classified destructive, so the bridge queues it
for you instead of running it.

**Step 6 — three packages, three verdicts.** All real policy decisions; a
non-zero `exit_code` usually means the platform worked.

| `{"package": "six"}` | allows | on TrustGate's baseline allowlist |
|---|---|---|
| `{"package": "colourama"}` | blocks | a real supply-chain incident, denylisted |
| `{"package": "docopt"}` | holds | on neither list, so policy asks you |

After the hold, the request is waiting in **Security → TrustGate approvals**.
If `infra_failure` comes back non-null, TrustGate *failed* rather than
decided — it fails closed, so that state otherwise looks like a block.

**Step 7 — then make it succeed.** Shipped policy is "block all except
listed", with an empty list, so `{"host": "example.com"}` returns
`status: 403`. Add `example.com` to the app's **Allowed destinations**, save,
and call again for `status: 200`. The before-and-after is the point.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. AgentBox polls this. |
| POST | `/process` | Summarise a document. |
| POST | `/process/stream` | The same, as server-sent events. |
| POST | `/process/upload` | The same, from an uploaded text file. |
| POST | `/mcp/save-note` | Call the bundled MCP server. |
| POST | `/mcp/list-notes` | Read the notes back. |
| POST | `/mcp/delete-all-notes` | Destructive — held for approval. |
| POST | `/demo/install-package` | Install a package via TrustGate. |
| POST | `/demo/fetch-url` | Attempt egress via SecureProxy. |
| POST | `/demo/touch-agent-file` | Write a flagged file for Defender. |

Listens on **8080**, declared in `agentbox-config.yaml`.

## How it is wired

- **Model access.** The app holds no provider key. AgentBox injects a
  SecureProxy URL and a per-app virtual key, and the backend binds
  `ANTHROPIC_API_KEY` to them at startup. Direct provider calls are refused.
- **Packages.** Runtime `pip` installs go through TrustGate, which
  resolves the tree, scans the artifact, checks advisories, then allows,
  holds, or blocks.
- **Skills.** Approved skills are mounted read-only under
  `AGENTBOX_SKILLS_ROOT`. The app sees only its own.
- **MCP.** `mcp/notes-server/` runs as its own container, reachable only
  through MCP Bridge.
- **Egress.** Whitelist with an empty list: the model, and nothing else.

## Layout

```
agentbox-config.yaml   what AgentBox needs to know
openapi.json           enables the Try API console
app/main.py            the routes
app/demos.py           the TrustGate and egress probes
app/backends/          one file per agent SDK
mcp/notes-server/      a real MCP server, own container
skills/                one bundled skill
```

## Changing a route

Regenerate the API document, or the console falls out of step:

```bash
python -c "import json, sys; sys.path.insert(0, '.'); from app.main import app; \\
  print(json.dumps(app.openapi(), indent=2))" > openapi.json
```


## Licence

Apache-2.0. See [LICENSE](LICENSE).
