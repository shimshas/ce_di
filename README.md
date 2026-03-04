# DI_CE — Device Intelligence Plugin Generator (Agentic AI)

An agentic AI system that automates Netskope Device Intelligence plugin development.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Interactive mode — just tell it what you want
python -m di_ce.cli

# One-shot generation
python -m di_ce.cli generate --platform "Infoblox" --api-type rest --auth basic
```

## How It Works

1. **Tell the agent** which platform you want a plugin for and what data you need
2. The agent **analyzes the API** (auth type, pagination, response format)
3. It **generates a complete plugin** — `main.py`, `manifest.json`, `__init__.py`, `CHANGELOG.md`
4. All generated code follows the **exact Netskope DI SDK patterns**

## Architecture

```
di_ce/
├── cli.py                  # Interactive CLI — talk to the agent
├── agent/
│   ├── orchestrator.py     # Main agent brain — routes user intent
│   ├── planner.py          # Breaks down plugin requirements into steps
│   └── validator.py        # Validates generated plugins
├── analyzer/
│   ├── api_schema.py       # Analyzes API specs (auth, pagination, fields)
│   └── field_mapper.py     # Maps API fields → Asset model fields
├── generator/
│   ├── plugin_generator.py # Assembles the final plugin from templates
│   ├── manifest_gen.py     # Generates manifest.json
│   └── main_gen.py         # Generates main.py with full plugin class
├── templates/
│   ├── base_plugin.py.j2   # Jinja2 template for main.py
│   └── manifest.json.j2    # Jinja2 template for manifest.json
├── knowledge/
│   ├── asset_model.py      # Asset model field definitions & constraints
│   ├── auth_patterns.py    # Known auth patterns (basic, oauth2, token)
│   └── pagination.py       # Known pagination patterns (cursor, offset, link)
└── config.py               # Configuration constants
```

## Supported Auth Types
- HTTP Basic Auth (username/password)
- OAuth2 Client Credentials (client_id/client_secret/tenant_id)
- API Token (Bearer/custom header)

## Supported Pagination
- Cursor-based (`_page_id`, `next_page_id`)
- Offset-based (`offset`, `limit`)
- Link-based (`@odata.nextLink`, `next` URL)

## Example Session

```
$ python -m di_ce.cli

🤖 DI Plugin Generator Agent
   Tell me which platform and what data you need.

> I need a plugin for CrowdStrike that pulls device assets using their OAuth2 API.
  I want hostname, mac_address, os, os_version, serial_number, manufacturer, model_name.

🔍 Analyzing requirements...
   Platform: CrowdStrike
   Auth: OAuth2 Client Credentials
   Data: Assets (7 fields mapped)

📦 Generating plugin: crowdstrike_iot/
   ✓ manifest.json
   ✓ __init__.py
   ✓ main.py (CrowdStrikePlugin class)
   ✓ CHANGELOG.md

✅ Plugin generated at: ./output/crowdstrike_iot/
```
