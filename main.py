import argparse
import json
from pathlib import Path

from migrator import MigrationOrchestrator


def main() -> int:
    parser = argparse.ArgumentParser(description="Run phased Jira migration steps")
    parser.add_argument("--config", default="config/config.json", help="Path to the JSON config file")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (Path(__file__).resolve().parent / config_path).resolve()

    if not config_path.exists():
        print(f"Configuration file not found: {config_path}")
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    orchestrator = MigrationOrchestrator(config)
    orchestrator.run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
