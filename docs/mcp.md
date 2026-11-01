# Use it from an AI agent (MCP)

domainhack runs as a [Model Context Protocol](https://modelcontextprotocol.io/) server, so
Claude, Cursor or any MCP client can look for a domain while it helps you name a project.
The server exposes the same features as the CLI, as four tools:

| Tool | What it does | Network |
|---|---|---|
| `find_domain_for_name` | What `domainhack name` does: exact name under a TLD preset, domain hacks that spell it, optional brand variants, ranked recommendation, details for taken names | registries |
| `check_domains` | Availability and registration details for specific names (`pla.to`, `foto.com.ar`), with the DNS cross-check | registries |
| `find_hacks_for_words` | Which words end in a TLD and the hack each forms (`testing` -> `test.ing`) | none |
| `list_tlds` | Which TLDs can be checked, how, and why not | none |

## Install

```bash
pipx install "domainhack[mcp]"      # adds the domainhack-mcp command
# or run it on demand, without installing:
uvx --from "domainhack[mcp]" domainhack-mcp
```

The server speaks MCP over stdio. It accepts the run options the CLI shares
(`--delay`, `--parallel`, `--no-cache`, `--cache-path`, `--contact EMAIL`); the progress
bar is always off. Results are cached like the CLI's, so repeated questions do not hit the
registries again.

## Claude Code

```bash
claude mcp add domainhack -- uvx --from "domainhack[mcp]" domainhack-mcp
```

Then ask, for example: *"I'm calling this project sumanda. Find me a domain."* Claude calls
`find_domain_for_name` and reads the recommendation back, including why the `.com` is not an
option (taken, parked, expiring) and which hacks are free.

## Claude Desktop

Add to `claude_desktop_config.json` (Settings, Developer, Edit Config):

```json
{
  "mcpServers": {
    "domainhack": {
      "command": "uvx",
      "args": ["--from", "domainhack[mcp]", "domainhack-mcp"]
    }
  }
}
```

## Cursor and other clients

Cursor reads the same shape from `.cursor/mcp.json` (project) or `~/.cursor/mcp.json`
(global). Any client that can launch a stdio server works: the command is `domainhack-mcp`
(or the `uvx` form above) and there is nothing else to configure.

To send a contact address with RDAP requests, as the CLI's `--contact` does, add
`"args": ["--from", "domainhack[mcp]", "domainhack-mcp", "--contact", "you@example.com"]`
or set `DOMAINHACK_CONTACT` in the server's `env`.

## What the agent gets back

`find_domain_for_name` returns the report as structured data: `exact`, `hacks`, `variants`,
`taken`, `unverifiable` and `dns_conflicts` entries (each with `domain`, `status`, `detail`
and, for taken names, `statuses`, `registered_at`, `expires_at`, `registrar`, `parked_hint`),
the `recommendations` with their reason, `advice`, `notes`, the check `summary` and any
`warnings` (an unsupported TLD, for instance). Only `available` names are recommended;
`dns-conflict`, `dropping` and `taken` ones are not free.

The server's instructions tell the model that "available" means "not registered" and that
premium or reserved names may still show as available, so it should suggest confirming with
a registrar before buying.

## Being a good citizen, still

Everything in [Usage](usage.md#being-a-good-citizen) applies: one request in flight per
registry host, adaptive backoff, `Retry-After` honoured, a circuit breaker per host and an
identifying `User-Agent`. An agent asking for a name under the `startup` preset makes a few
dozen queries spread over eight registries; `all-supported` asks every registry the tool knows
and takes minutes. The tool descriptions say so.
