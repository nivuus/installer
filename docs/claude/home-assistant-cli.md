# Home Assistant CLI (/usr/local/bin/ha)

### Home Assistant CLI (`/usr/local/bin/ha`)

Bash CLI for the Home Assistant REST + WebSocket API. Covers entity state management, service calls, and full CRUD on automations, scripts, and dashboards. Uses `curl` for REST, embedded Python + `aiohttp` for WebSocket (traces, dashboards). Output is colorized with `jq` formatting.

**CRITICAL**: NEVER edit `.storage/` files directly. Always use the `ha` CLI to modify Home Assistant configuration (dashboards, automations, etc.). Direct file edits can corrupt HA state or be overwritten.

**Config**: `/root/.config/nivuus/ha.conf` (`HA_URL` + `HA_TOKEN`), falls back to env vars or localhost defaults.

```bash
# Entity & state management
ha states                              # List all entity states (JSON)
ha states <entity_id>                  # Get specific entity state
ha set <entity_id> <state> [attr_json] # Set entity state + optional attributes
ha call <entity_id> <action> [data]    # Shortcut: call service on entity (auto-detects domain)
ha service <domain> <service> [data]   # Call any HA service
ha history <entity_id> [duration]      # State history (duration: Nh/Nd/Nm, default 24h)
ha log                                 # View HA error log
ha config                              # Get HA configuration
ha events <type> [data]                # Fire a custom event
ha template '<jinja2>'                 # Render a Jinja2 template
ha raw <endpoint> [method] [data]      # Raw REST API call (any endpoint)

# Automations (REST + WebSocket for traces/categories)
ha automation list                     # Table: entity_id, state (on/off), last_triggered, category, name
ha automation get <entity_id>          # Get config JSON (uses config ID from attributes)
ha automation enable <entity_id>       # Turn on
ha automation disable <entity_id>      # Turn off
ha automation trigger <entity_id>      # Trigger immediately
ha automation edit <entity_id> <file|->  # Update config from file or stdin
ha automation rename <entity_id> <name>  # Rename automation (updates alias in config)
ha automation category <entity_id>     # Get current category
ha automation category <entity_id> <name>  # Set category (auto-creates if needed, uses WebSocket entity/category registry)
ha automation icon <entity_id>         # Get current icon
ha automation icon <entity_id> <icon>  # Set icon (mdi:...), use "" to remove
ha automation create <file|->          # Create new (auto-generates timestamp config ID)
ha automation delete <entity_id> [-y]  # Delete (-y skips confirmation)
ha automation trace <entity_id>        # Execution traces via WebSocket (timestamp, state, trigger)
ha automation reload                   # Reload YAML automations

# Scripts (REST + WebSocket for traces)
ha script list                         # Table: entity_id, state (off/running), last_triggered, name
ha script get <entity_id>              # Get config JSON (slug = object_id, no attributes lookup)
ha script trigger <entity_id>          # Run script (script.turn_on)
ha script edit <entity_id> <file|->    # Update config from file or stdin
ha script create <script_id> <file|->  # Create new (user provides slug, becomes script.<slug>)
ha script delete <entity_id> [-y]      # Delete (-y skips confirmation)
ha script trace <entity_id>            # Execution traces via WebSocket
ha script reload                       # Reload YAML scripts

# Scenes (REST - same pattern as automations)
ha scene list                          # Table: entity_id, friendly_name
ha scene get <entity_id>               # Get config via attributes.id lookup
ha scene activate <entity_id>          # Activate scene (with optional --transition N)
ha scene edit <entity_id> <file|->     # Update config from file or stdin
ha scene create <file|->               # Create new (auto-generates timestamp config ID)
ha scene delete <entity_id> [-y]       # Delete (-y skips confirmation)
ha scene reload                        # Reload YAML scenes

# Blueprints (WebSocket only)
ha blueprint list [domain]             # Table: path, name, domain (default: automation)
ha blueprint get <path> [domain]       # Get blueprint details from list result
ha blueprint import <url> [domain]     # Import from URL + auto-save
ha blueprint delete <path> [domain] [-y]  # Delete (-y skips confirmation)

# Dashboards (WebSocket only - Lovelace)
ha dashboard list                      # Table: url_path, title, mode, sidebar, admin
ha dashboard get [url_path]            # Get config JSON (default dashboard if omitted)
ha dashboard set <url_path> <file|->   # Update from file or stdin
ha dashboard delete <url_path> [-y]    # Delete (-y skips confirmation)
```

**Key implementation details**:
- Entity ID prefix is auto-added: `ha script get my_script` → looks up `script.my_script`
- Automations and scenes use a config ID stored in `attributes.id` (API lookup required); scripts use the object_id directly
- `automation create` and `scene create` auto-generate a timestamp ID; `script create` requires an explicit slug
- File arguments accept `-` for stdin: `echo '{"alias":"test"}' | ha script create my_id -`
- All delete commands prompt for confirmation unless `-y` is passed
- Blueprints default to `automation` domain; pass `script` as second arg for script blueprints
- `blueprint import` fetches + auto-saves via `blueprint/save` WebSocket call
