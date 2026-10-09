"""Optional, model-free vault link bookkeeping and a standalone watch command."""
import argparse
from collections.abc import Callable
import json
import math
import os
from pathlib import Path
import sys
import threading

from dotenv import dotenv_values
from obsidian_mcp.link_integrity import AuditCancelled, LinkIndex, Policy

ENV_DIR = Path(__file__).resolve().parents[3]
INTERVAL_SECONDS = 30.0


class VaultLinkAuditor:
    def __init__(self, vault: Path, *, policy_path: Path | None = None,
                 interval: float = INTERVAL_SECONDS):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Audit interval must be finite and positive')
        self.policy_path = policy_path
        self.interval = interval
        self.latest: dict | None = None
        self.error: str | None = None
        self.changes = 0
        self.on_change: Callable[[dict], None] | None = None
        self._stop = threading.Event()
        self._index = LinkIndex(vault, should_stop=self._stop.is_set)
        self._thread = threading.Thread(target=self._run, name='nuru-vault-links', daemon=True)
        self._pass_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> 'VaultLinkAuditor | None':
        env = {**dotenv_values(ENV_DIR / '.env.obsidian'),
               **dotenv_values(ENV_DIR / '.env.nuru'), **os.environ}
        if env.get('NURU_LINK_AUDIT') != '1' or not env.get('VAULT_PATH'):
            return None
        path = env.get('OBSIDIAN_LINK_POLICY')
        interval = float(env.get('NURU_LINK_AUDIT_INTERVAL') or INTERVAL_SECONDS)
        if interval < 1:
            raise ValueError('NURU_LINK_AUDIT_INTERVAL must be at least 1 second')
        return cls(Path(env['VAULT_PATH']), policy_path=Path(path).expanduser() if path else None, interval=interval)

    def run_once(self) -> dict:
        with self._pass_lock:
            self._index.policy = Policy.load(self.policy_path)
            report = self._index.report()
            changed = self.latest is None or self.latest['snapshot'] != report['snapshot']
            self.latest = report
            self.error = None
            if changed:
                self.changes += 1
                if self.on_change:
                    self.on_change(report)
            return report

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(5.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except AuditCancelled:
                break
            except Exception as exc:
                message = f'{type(exc).__name__}: {exc}'
                if message != self.error:
                    print(f'Nuru: link audit unavailable: {message}', file=sys.stderr)
                self.error = message
            self._stop.wait(self.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description='Model-free read-only vault link audit; optional background polling.')
    parser.add_argument('vault', type=Path)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=INTERVAL_SECONDS)
    args = parser.parse_args()
    worker = VaultLinkAuditor(args.vault, policy_path=args.policy, interval=args.interval)
    if not args.watch:
        print(json.dumps(worker.run_once(), ensure_ascii=False, indent=2))
        return
    worker.on_change = lambda report: print(json.dumps(report, ensure_ascii=False), flush=True)
    worker.start()
    try:
        # The worker waits interruptibly; this is a user-run CLI, not a tool sleep.
        while worker._thread.is_alive():
            worker._thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


if __name__ == '__main__':
    main()
