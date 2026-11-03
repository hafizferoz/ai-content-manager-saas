# AI Provider Testing

Run API tests from `apps/api`:

```powershell
python -m pytest -q tests/test_ai_provider.py tests/test_ai_provider_config.py tests/test_ai_workflows.py tests/test_migrations.py
```

Tests mock provider HTTP responses and do not require real keys. Coverage
includes Gemini/OpenAI-compatible request and usage parsing, secret-safe
errors, encrypted workspace key persistence, role-protected configuration,
model discovery, transient fallback, permanent authentication failure without
fallback, workflow metadata, and migrations.

Live provider quality baselines are an operator-controlled task and must use
synthetic/non-customer inputs. Never place production credentials in test
fixtures or test output.
