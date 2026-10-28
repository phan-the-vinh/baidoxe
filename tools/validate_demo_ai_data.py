import argparse
import json
from pathlib import Path

from demo_ai_data import DEFAULT_RUN_DIR, SLOT_IDS, validate_demo_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra dữ liệu AI demo, không truy cập SQL Server.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    args = parser.parse_args()
    data = validate_demo_data(args.run_dir)
    report = {
        "valid": True,
        "run_dir": str(data.run_dir),
        "frames": len(data.state_rows),
        "slots": len(SLOT_IDS),
        "state_records": len(data.state_rows) * len(SLOT_IDS),
        "transitions": len(data.transition_rows),
        "hashes": data.hashes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

