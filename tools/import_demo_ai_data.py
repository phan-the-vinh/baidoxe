from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from demo_ai_data import DEFAULT_RUN_DIR, SLOT_IDS, validate_demo_data


ROOT = Path(__file__).resolve().parents[1]
TARGET_DATABASE = "ParkingAI_Demo"
DEFAULT_SOURCE_KEY = "0922_1_x01_d16_fix_v1"


def odbc_escape(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def connect_target():
    import pyodbc

    load_dotenv(ROOT / ".env")
    values = {
        "DRIVER": os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        "SERVER": os.getenv("DB_SERVER", r"localhost\SQLEXPRESS"),
        "DATABASE": TARGET_DATABASE,
        "Encrypt": os.getenv("DB_ENCRYPT", "yes"),
        "TrustServerCertificate": os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes"),
        "Connection Timeout": "8",
    }
    if os.getenv("DB_TRUSTED_CONNECTION", "true").lower() == "true":
        values["Trusted_Connection"] = "yes"
    else:
        user = os.getenv("DB_USER", "")
        password = os.getenv("DB_PASSWORD", "")
        if not user or not password:
            raise RuntimeError("Thiếu DB_USER/DB_PASSWORD cho SQL Server authentication.")
        values["UID"] = user
        values["PWD"] = password
    connection_string = ";".join(
        f"{key}={odbc_escape(str(value))}" for key, value in values.items()
    )
    return pyodbc.connect(connection_string, autocommit=False)


def pipeline_config(summary: dict) -> str:
    exclusion = dict(summary.get("exclusion") or {})
    exclusion.pop("audit_csv", None)
    value = {
        "logic_revision": "d16_evidence_fix",
        "thresholds": summary.get("thresholds"),
        "exclusion": exclusion,
        "checks": summary.get("checks"),
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def import_data(data, source_key: str) -> tuple[int, int, int]:
    connection = connect_target()
    try:
        cursor = connection.cursor()
        database_name = cursor.execute("SELECT DB_NAME()").fetchval()
        if database_name != TARGET_DATABASE:
            raise RuntimeError(f"Từ chối import vào database {database_name!r}.")
        required = ("demo_videos", "demo_slot_states", "demo_slot_transitions")
        for table in required:
            exists = cursor.execute(
                "SELECT OBJECT_ID(?, 'U')", f"dbo.{table}"
            ).fetchval()
            if exists is None:
                raise RuntimeError(f"Chưa có bảng dbo.{table}; script import không tự tạo schema.")

        summary = data.summary
        source = summary["source"]
        output = summary["output_validation"]
        metadata = (
            source_key,
            Path(summary["input_video"]).name,
            "yolo_polygon_annotated_h264.mp4",
            str(data.run_dir.relative_to(ROOT)).replace("\\", "/"),
            int(source["width"]),
            int(source["height"]),
            int(output["width"]),
            int(output["height"]),
            str(source["fps"]),
            int(source["frames"]),
            str(source["duration_seconds"]),
            len(SLOT_IDS),
            data.hashes["states_csv"],
            data.hashes["transitions_csv"],
            data.hashes["processed_video"],
            pipeline_config(summary),
        )
        current = cursor.execute(
            "SELECT video_id, frame_count, states_csv_sha256, "
            "transitions_csv_sha256, processed_video_sha256 "
            "FROM dbo.demo_videos WHERE source_key = ?",
            source_key,
        ).fetchone()
        if current is None:
            video_id = cursor.execute(
                "INSERT INTO dbo.demo_videos ("
                "source_key, source_video_name, processed_video_name, pipeline_run_dir, "
                "source_width, source_height, output_width, output_height, fps, frame_count, "
                "duration_seconds, slot_count, states_csv_sha256, transitions_csv_sha256, "
                "processed_video_sha256, pipeline_config_json) "
                "OUTPUT INSERTED.video_id VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                *metadata,
            ).fetchval()
        else:
            video_id = int(current[0])
            expected_identity = (
                int(source["frames"]),
                data.hashes["states_csv"],
                data.hashes["transitions_csv"],
                data.hashes["processed_video"],
            )
            if tuple(current[1:]) != expected_identity:
                raise RuntimeError("source_key đã tồn tại nhưng metadata/checksum khác; từ chối ghi đè.")

        source_states = {
            (row["frame_index"], slot): (
                row["timestamp_seconds"], row["states"][slot]
            )
            for row in data.state_rows
            for slot in SLOT_IDS
        }
        existing_states = {
            (int(row[0]), row[2]): (row[1], row[3])
            for row in cursor.execute(
                "SELECT frame_index, timestamp_seconds, slot_code, state "
                "FROM dbo.demo_slot_states WHERE video_id = ?",
                video_id,
            ).fetchall()
        }
        conflicts = {
            key for key, value in existing_states.items()
            if key not in source_states or source_states[key] != value
        }
        if conflicts:
            raise RuntimeError(f"Có {len(conflicts)} bản ghi state xung đột; đã rollback.")
        missing_states = [
            (video_id, frame, timestamp, slot, state)
            for (frame, slot), (timestamp, state) in source_states.items()
            if (frame, slot) not in existing_states
        ]
        if missing_states:
            cursor.fast_executemany = True
            cursor.executemany(
                "INSERT INTO dbo.demo_slot_states "
                "(video_id, frame_index, timestamp_seconds, slot_code, state) "
                "VALUES (?,?,?,?,?)",
                missing_states,
            )

        source_transitions = {
            (row["frame_index"], row["slot_code"]): (
                row["timestamp_seconds"],
                row["previous_state"],
                row["new_state"],
                row["evidence"],
            )
            for row in data.transition_rows
        }
        existing_transitions = {
            (int(row[0]), row[2]): (row[1], row[3], row[4], row[5])
            for row in cursor.execute(
                "SELECT frame_index, timestamp_seconds, slot_code, previous_state, "
                "new_state, evidence FROM dbo.demo_slot_transitions WHERE video_id = ?",
                video_id,
            ).fetchall()
        }
        conflicts = {
            key for key, value in existing_transitions.items()
            if key not in source_transitions or source_transitions[key] != value
        }
        if conflicts:
            raise RuntimeError(f"Có {len(conflicts)} transition xung đột; đã rollback.")
        missing_transitions = [
            (video_id, frame, timestamp, slot, previous, new, evidence)
            for (frame, slot), (timestamp, previous, new, evidence)
            in source_transitions.items()
            if (frame, slot) not in existing_transitions
        ]
        if missing_transitions:
            cursor.fast_executemany = True
            cursor.executemany(
                "INSERT INTO dbo.demo_slot_transitions "
                "(video_id, frame_index, timestamp_seconds, slot_code, "
                "previous_state, new_state, evidence) VALUES (?,?,?,?,?,?,?)",
                missing_transitions,
            )
        connection.commit()
        return video_id, len(missing_states), len(missing_transitions)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mặc định chỉ kiểm tra CSV; chỉ import khi có đủ cờ xác nhận."
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--confirm-import")
    args = parser.parse_args()
    data = validate_demo_data(args.run_dir)
    print(
        f"VALID: {len(data.state_rows)} frames, "
        f"{len(data.state_rows) * len(SLOT_IDS)} states, "
        f"{len(data.transition_rows)} transitions."
    )
    if not args.execute:
        print("DRY RUN: no SQL Server connection and no database writes.")
        return 0
    if args.confirm_database != TARGET_DATABASE or args.confirm_import != "IMPORT_FINAL_DEMO_CSV":
        raise SystemExit(
            "Thiếu xác nhận kép: --confirm-database ParkingAI_Demo "
            "--confirm-import IMPORT_FINAL_DEMO_CSV"
        )
    video_id, states, transitions = import_data(data, args.source_key)
    print(f"IMPORTED: video_id={video_id}, states={states}, transitions={transitions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
