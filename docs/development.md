# Development Guide

This guide walks you through setting up your development environment and contributing code to the Zendure HA integration.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Environment Setup](#environment-setup)
- [Running the Integration](#running-the-integration)
- [Adding a New Device](#adding-a-new-device)
- [Testing & Debugging](#testing--debugging)
- [Code Style & Quality](#code-style--quality)
- [Submitting Changes](#submitting-changes)

---

## Prerequisites

- **Git** (for cloning & branching)
- **Docker & Docker Compose** (for dev container)
- **VS Code** (recommended; dev container config included)
- **Home Assistant knowledge** (read [`how-it-works.md`](how-it-works.md) first)

Optional but helpful:
- MQTT client (for monitoring MQTT traffic)
- Python 3.12+ (for local linting)

---

## Environment Setup

### 1. Clone the Repository

```bash
git clone https://github.com/zendure/zendure-ha.git
cd zendure-ha
```

### 2. Install Dependencies

```bash
# Run the setup script (works on Linux/macOS; Windows users use WSL2)
scripts/setup
```

This installs:
- Python dev tools (black, pylint, pytest)
- Home Assistant test environment
- Dev container dependencies

### 3. (Optional) Configure Local MQTT

For faster testing, set up a local MQTT broker:

```bash
# Start MQTT container
docker run -d -p 1883:1883 --name mosquitto eclipse-mosquitto
```

Then enable in Home Assistant config:
```yaml
# config/configuration.yaml (if testing legacy devices)
mqtt:
  broker: 127.0.0.1
  port: 1883
```

---

## Running the Integration

### Start the Dev Environment

```bash
scripts/develop
```

This:
1. Builds a Home Assistant Docker container with the integration mounted
2. Exposes Home Assistant at `http://localhost:8123`
3. Mounts your code as a volume (changes reload automatically)

### First-Time Setup

1. **Open** http://localhost:8123
2. **Onboard** Home Assistant (set name, location, etc.)
3. **Add P1 Meter** (if testing Smart Mode):
   - Go to **Settings → Devices & Services → Create Automation**
   - Create a dummy sensor for testing:
     ```yaml
     template:
       - sensor:
           - name: "P1 Meter (Test)"
             unique_id: p1_meter_test
             unit_of_measurement: "W"
             state: "{{ range(-1000, 1000) | random }}"
     ```
4. **Add Zendure Integration**:
   - Go to **Settings → Devices & Services → Create Integration**
   - Search for "Zendure"
   - Enter your token (or use a dummy for testing)
   - Select P1 Meter entity

### View Logs

Home Assistant logs appear in the container. View them:

```bash
# In the running terminal
# OR go to http://localhost:8123 → Settings → System → Logs
```

Enable debug logging for the integration:
```yaml
# In UI: Settings → System → Logs
logger:
  logs:
    custom_components.zendure_ha: debug
```

---

## Adding a New Device

### Step 1: Create Device Class

Create a new file in `custom_components/zendure_ha/devices/`:

```python
# devices/mynewdevice.py
from .device import ZendureZenSdk
from ..const import DeviceState

class MyNewDevice(ZendureZenSdk):
    """My New Device implementation."""
    
    def __init__(self, api, device_data):
        super().__init__(api, device_data)
        self.name = "My New Device"
        self.product_id = "ZN-MYNEW"  # from Zendure API
    
    async def async_setup_entities(self):
        """Register entities for this device."""
        # Call parent setup
        await super().async_setup_entities()
        
        # Add any device-specific entities here
        # (most devices use the default set from parent)
```

### Step 2: Register in api.py

Update `api.py` to instantiate your device:

```python
# custom_components/zendure_ha/api.py
from .devices.mynewdevice import MyNewDevice

async def get_devices(api_client):
    devices = []
    for device_data in api_response['devices']:
        product_id = device_data.get('productId')
        
        if product_id == 'ZN-MYNEW':
            device = MyNewDevice(api_client, device_data)
        elif product_id == 'SolarFlow':
            device = SolarFlow(api_client, device_data)
        # ... other devices
        else:
            _LOGGER.warning("Unknown device type: %s", product_id)
            continue
        
        devices.append(device)
    return devices
```

### Step 3: Add to manifest.json

Update the README and HACS manifest to list the new device:

```json
// manifest.json
{
  "name": "Zendure Home Assistant Integration",
  // ... other fields
}
```

```markdown
# README.md
## Supported Devices

### Zendure ZenSDK Devices (MQTT + HTTP fallback)

- **SolarFlow series**: ...
- **My New Device** ← Add here
- **SuperBase V4600**: ...
```

### Step 4: Test

```bash
scripts/develop
# Add integration with test token
# Check that your device appears and its entities work
```

### Step 5: Verify With Actual Data

If you have access to a real device:

1. Monitor MQTT traffic:
   ```bash
   mosquitto_sub -h mqtt.zendure.tech -u your_account -P your_password -t 'BC8B7F/My Device/#' -v
   ```

2. Extract device properties and map them in your device class

3. Test commands (`power_charge`, `power_discharge`, etc.)

---

## Testing & Debugging

### Enable Debug Logging

```yaml
# In Home Assistant UI: Settings → System → Logs
logger:
  default: info
  logs:
    custom_components.zendure_ha: debug
    custom_components.zendure_ha.mqtt_protocol: debug
    custom_components.zendure_ha.power_strategy: debug
```

Then reproduce the issue and grab the logs.

### Monitor MQTT Traffic

```bash
# Cloud MQTT (requires account credentials)
mosquitto_sub -h mqtt.zendure.tech \
  -u your_email@example.com \
  -P your_password \
  -t 'BC8B7F/#' -v

# Local MQTT (if configured)
mosquitto_sub -h 127.0.0.1 -t 'BC8B7F/#' -v
```

### Inspect Device State

Home Assistant stores device state in the **States** page:

1. Go to **Developer Tools → States**
2. Search for your device
3. View current entity values and attributes

### Unit Tests (Optional)

The project uses pytest. Run tests:

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all tests
pytest

# Run specific test
pytest tests/test_power_strategy.py -v
```

---

## Code Style & Quality

### Format Code

Use black for consistent formatting:

```bash
scripts/lint  # Runs black, pylint, type checks
```

### Type Hints

Include type hints for new functions:

```python
# ❌ Before
def classify_device(device, p1):
    return device.soc > 50

# ✅ After
def classify_device(device: ZendureDevice, p1: int) -> bool:
    """Classify device; return True if should charge."""
    return device.electricLevel.asInt > 50
```

### Docstrings

For public functions/classes:

```python
class MyClass:
    """Short description of the class.
    
    Longer explanation if needed. Mention key methods
    and what this class does.
    """
    
    async def important_method(self, param: str) -> bool:
        """Short description.
        
        Args:
            param: Description of param
            
        Returns:
            True if successful, False otherwise.
            
        Raises:
            ValueError: If param is invalid.
        """
```

### Constants

Keep magic numbers in `const.py`:

```python
# ❌ Avoid scattered numbers
if device.soc > 95:
    stop_charge()

# ✅ Use constants
if device.soc > SmartMode.SOC_TARGET:
    stop_charge()
```

---

## Submitting Changes

### Before You Commit

1. **Test locally**: Run `scripts/develop` and verify functionality
2. **Run linter**: `scripts/lint` (must pass)
3. **Update docs**: If behavior changed, update relevant `.md` files
4. **Write commit message**: See [CONTRIBUTING.md](../CONTRIBUTING.md)

### Create a Pull Request

1. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open PR** on GitHub:
   - Title: Short, descriptive (50 chars max)
   - Description: Explain what changed and why
   - Link related issues: "Fixes #123"

3. **Respond to feedback**: Maintainers may request changes

4. **Celebrate!** 🎉 Once merged, your code is live

---

## Common Development Tasks

### Debugging Power Distribution

If power isn't distributing correctly:

1. Enable debug logs (see above)
2. Look for `Classify ... => CHARGE|DISCHARGE|IDLE` lines
3. Check `setpoint_delta` values sum up correctly
4. Verify FuseGroup limits aren't capping power
5. See [`smart-mode.md`](smart-mode.md) for algorithm details

### Adding a New Entity Type

Example: adding a new number entity for charge limit:

```python
# In your device class
from ..number import ZendureNumber

class MyDevice(ZendureZenSdk):
    async def async_setup_entities(self):
        # Register a number entity
        self.charge_limit = ZendureNumber(
            self,
            unique_id="charge_limit",
            name="Charge Limit",
            unit="W",
            min_value=0,
            max_value=self.max_charge,
            onchange=self.set_charge_limit
        )
```

### Testing MQTT Integration

If working on MQTT code:

```bash
# Monitor all messages
mosquitto_sub -h localhost -t '#' -v

# Publish test message
mosquitto_pub -h localhost -t 'test/topic' -m '{"power": 500}'
```

---

## Useful Resources

- **Architecture**: See [`architecture.md`](architecture.md)
- **Smart Mode algorithm**: See [`smart-mode.md`](smart-mode.md)
- **ZenSDK API**: See [`ZenSDK_Docs_merged_2026-04-05.md`](ZenSDK_Docs_merged_2026-04-05.md)
- **Power classification**: See [`power-classifications-description.md`](power-classifications-description.md)
- **Home Assistant docs**: https://developers.home-assistant.io/

---

## Getting Help

- **Stuck?** Open a [GitHub Discussion](https://github.com/zendure/zendure-ha/discussions)
- **Found a bug?** File an [Issue](https://github.com/zendure/zendure-ha/issues)
- **Want to contribute?** See [CONTRIBUTING.md](../CONTRIBUTING.md)

Happy coding! 🚀
