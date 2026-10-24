"""Opt-in model-free stewardship worker and standalone CLI."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import threading

from dotenv import dotenv_values
from obsidian_mcp.link_integrity import AuditCancelled
from obsidian_mcp.steward import Steward

ENV_DIR = Path(__file__).resolve().parents[3]


class VaultSteward:
    def __init__(self, vault: Path, config_path: Path, *, state_path: Path | None = None, interval: float = 30):
        if not math.isfinite(interval) or interval < 1:
            raise ValueError('Steward interval must be finite and at least 1 second')
        self.vault, self.config_path, self.state_path, self.interval = vault, config_path, state_path, interval
        self.latest = None
        self.error = None
        self.changes = 0
        self.on_change = None
        self._stop = threading.Event()
        self._pass_lock = threading.Lock()
        self._runner, self._config = None, None
        self._thread = threading.Thread(target=self._run, name='nuru-vault-steward', daemon=True)

    @classmethod
    def from_env(cls):
        env = {**dotenv_values(ENV_DIR / '.env.obsidian'), **dotenv_values(ENV_DIR / '.env.nuru'), **os.environ}
        if env.get('NURU_STEWARD_ENABLED') != '1':
            return None
        if not env.get('VAULT_PATH') or not env.get('OBSIDIAN_STEWARD_CONFIG'):
            raise ValueError('Steward requires VAULT_PATH and OBSIDIAN_STEWARD_CONFIG')
        return cls(Path(env['VAULT_PATH']).expanduser(), Path(env['OBSIDIAN_STEWARD_CONFIG']).expanduser(),
                   state_path=Path(env['NURU_STEWARD_STATE']).expanduser() if env.get('NURU_STEWARD_STATE') else None,
                   interval=float(env.get('NURU_STEWARD_INTERVAL') or '30'))

    def run_once(self):
        with self._pass_lock:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            if config != self._config or self._runner is None:
                self._runner = Steward(self.vault, config, state_path=self.state_path, should_stop=self._stop.is_set)
                self._config = config
            report = self._runner.run_once()
            changed = self.latest is None or self.latest['snapshot'] != report['snapshot']
            self.latest, self.error = report, None
            if changed:
                self.changes += 1
                if self.on_change:
                    self.on_change(report)
            return report

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(5)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except AuditCancelled:
                break
            except Exception as exc:
                message = f'{type(exc).__name__}: {exc}'
                if message != self.error:
                    print(f'Nuru: stewardship unavailable: {message}', file=sys.stderr)
                self.error = message
            self._stop.wait(self.interval)


def main():
    parser = argparse.ArgumentParser(description='Model-free vault stewardship; optional polling, no chat startup.')
    parser.add_argument('vault', type=Path)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=30)
    args = parser.parse_args()
    worker = VaultSteward(args.vault, args.config, state_path=args.state, interval=args.interval)
    if not args.watch:
        print(json.dumps(worker.run_once(), ensure_ascii=False, default=str, indent=2))
        return
    worker.on_change = lambda report: print(json.dumps(report, ensure_ascii=False, default=str), flush=True)
    worker.start()
    try:
        while worker._thread.is_alive():
            worker._thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


if __name__ == '__main__':
    main()
