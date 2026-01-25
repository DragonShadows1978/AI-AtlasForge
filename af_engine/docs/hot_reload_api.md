# Integration Hot-Reload API

The IntegrationManager provides a hot-reload API for dynamically managing integrations at runtime without restarting the engine.

## Use Cases

- **Development workflow**: Modify integration code and reload without restart
- **Dynamic configuration**: Add/remove integrations based on runtime conditions
- **Debugging**: Reload problematic integrations after fixing issues

## API Reference

### reload_integration(name: str) -> bool

Hot-reload a specific integration handler.

```python
from af_engine.integration_manager import IntegrationManager

mgr = IntegrationManager()
mgr.load_default_integrations()

# Reload a single integration
success = mgr.reload_integration('analytics')
if success:
    print("Integration reloaded successfully")
```

**What happens during reload:**
1. Current handler is unregistered
2. Handler's module is reimported (using `importlib.reload`)
3. Fresh handler instance is created
4. Handler is re-registered with updated code

**On failure:** Original handler is restored if possible.

### reload_all_integrations() -> Dict[str, bool]

Hot-reload all registered integrations.

```python
results = mgr.reload_all_integrations()
for name, success in results.items():
    status = "✓" if success else "✗"
    print(f"{status} {name}")
```

### add_integration_dynamically(module_name: str, class_name: str) -> bool

Add a new integration at runtime.

```python
# Add a custom integration
success = mgr.add_integration_dynamically(
    module_name='my_project.integrations.custom',
    class_name='CustomIntegration'
)
```

**Requirements:**
- Module must be importable
- Class must implement `IntegrationHandler` protocol
- Class must have a parameterless constructor

### remove_integration(name: str) -> bool

Remove an integration at runtime.

```python
# Remove an integration
mgr.remove_integration('afterimage')
```

### get_integration_info(name: str) -> Optional[Dict]

Get detailed information about an integration.

```python
info = mgr.get_integration_info('analytics')
# Returns:
# {
#     "name": "analytics",
#     "priority": "CRITICAL",
#     "available": True,
#     "subscriptions": ["response_received", "mission_started", ...],
#     "module": "af_engine.integrations.analytics",
#     "class": "AnalyticsIntegration"
# }
```

## Example: Development Workflow

```python
from af_engine.integration_manager import IntegrationManager

# Initialize
mgr = IntegrationManager()
mgr.load_default_integrations()

# Make code changes to analytics.py...

# Reload the changed integration
if mgr.reload_integration('analytics'):
    print("Changes applied!")
else:
    print("Reload failed - check logs")

# Verify it's still working
assert mgr.is_available('analytics')
```

## Example: Dynamic Feature Flags

```python
def configure_integrations(features: dict):
    """Configure integrations based on feature flags."""
    mgr = IntegrationManager()
    mgr.load_default_integrations()

    if not features.get('analytics_enabled', True):
        mgr.remove_integration('analytics')

    if features.get('custom_integration'):
        mgr.add_integration_dynamically(
            'custom_integrations.my_handler',
            'MyHandler'
        )

    return mgr
```

## Thread Safety

The hot-reload API is **not thread-safe**. Do not call reload methods while events are being emitted. Best practice is to reload during initialization or during known quiet periods.

## Limitations

1. **State loss**: Reloading discards any state in the handler instance
2. **Not atomic**: During reload, the integration is briefly unavailable
3. **Module dependencies**: If the integration imports other modules that change, those may need reloading too
4. **Circular imports**: Can cause issues if integration modules have circular dependencies

## Performance Notes

- Reload time is typically <100ms per integration
- `reload_all_integrations()` reloads sequentially, not in parallel
- Frequent reloads may cause slight performance overhead due to import machinery
