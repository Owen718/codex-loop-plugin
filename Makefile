.PHONY: test smoke json-check clean

PLUGIN := plugins/codex-loop
PYTHONPATH := $(PLUGIN)/scripts

test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(PYTHONPATH) python3 -m unittest discover -s $(PLUGIN)/tests -v

json-check:
	python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
	python3 -m json.tool $(PLUGIN)/.codex-plugin/plugin.json >/dev/null
	python3 -m json.tool $(PLUGIN)/.mcp.json >/dev/null
	python3 -m json.tool $(PLUGIN)/hooks.json >/dev/null

smoke:
	tmpdb=$$(mktemp /tmp/codex-loop.XXXXXX.sqlite3); \
	PYTHONDONTWRITEBYTECODE=1 $(PLUGIN)/scripts/codex-loop --db "$$tmpdb" create 1m smoke --thread-id smoke --cwd "$$PWD" >/dev/null; \
	PYTHONDONTWRITEBYTECODE=1 $(PLUGIN)/scripts/codex-loop --db "$$tmpdb" list --thread-id smoke; \
	rm -f "$$tmpdb"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
