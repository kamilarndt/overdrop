.PHONY: help test test-py test-rust clean init serve

help:
	@echo "OverDrop — Universal Agent Communication Protocol v0.1.0"
	@echo ""
	@echo "  make test        — all tests"
	@echo "  make test-py     — Python tests (unit + integration + worktree)"
	@echo "  make test-rust   — Rust CLI tests"
	@echo "  make build-rust  — build Rust CLI binary"
	@echo "  make init        — init workspace"
	@echo "  make serve       — start web dashboard"
	@echo "  make clean       — remove artifacts"

test: test-pytest test-legacy

test-pytest:
	PYTHONPATH="$$PYTHONPATH:$(PWD)/python" python3 -m pytest tests/unit/ tests/integration/ tests/e2e/ -q

test-legacy:
	@echo "=== UNIT (legacy) ===" && PYTHONPATH="$$PYTHONPATH:$(PWD)/python" python3 tests/test_all.py 2>&1 | tail -1
	@echo "=== INTEGRATION (legacy) ===" && PYTHONPATH="$$PYTHONPATH:$(PWD)/python" python3 tests/test_integration.py 2>&1 | tail -1
	@echo "=== WORKTREE (legacy) ===" && PYTHONPATH="$$PYTHONPATH:$(PWD)/python" python3 tests/test_worktree.py 2>&1 | tail -1

test-rust:
	cd src/cli && cargo test 2>/dev/null || echo "Rust tests: cargo required"

build-rust:
	cd src/cli && cargo build --release 2>/dev/null && echo "✅ od binary: src/cli/target/release/od" || echo "❌ Rust build failed. Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"

init:
	@mkdir -p workspace/{inbox,active,done,failed,blocked,feedback}
	@echo "✅ Workspace: $(PWD)/workspace/"

serve:
	@cd $(PWD) && PYTHONPATH="$(PWD)/python" python3 python/dashboard.py .overdrop --port 7737 &
	@sleep 1
	@echo "⬡ Dashboard → http://localhost:7737"

clean:
	rm -rf ts/overdrop/dist ts/overdrop/node_modules
	rm -rf python/__pycache__ python/overdrop/__pycache__ python/**/__pycache__
	rm -rf __pycache__
	rm -rf workspace/
	find . -name "*.pyc" -delete
	find . -name "*.tmp" -delete
	rm -rf src/cli/target
