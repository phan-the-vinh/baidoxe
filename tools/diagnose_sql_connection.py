import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def odbc_escape(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    import pyodbc

    server = os.getenv("DB_SERVER", r"localhost\SQLEXPRESS")
    database = os.getenv("DB_NAME", "ParkingAI")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    trusted = os.getenv("DB_TRUSTED_CONNECTION", "true").lower() == "true"
    values = {
        "DRIVER": driver,
        "SERVER": server,
        "DATABASE": database,
        "Encrypt": os.getenv("DB_ENCRYPT", "yes"),
        "TrustServerCertificate": os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes"),
        "Connection Timeout": "3",
    }
    if trusted:
        values["Trusted_Connection"] = "yes"
    else:
        values["UID"] = os.getenv("DB_USER", "")
        values["PWD"] = os.getenv("DB_PASSWORD", "")

    print(f"SERVER={server}")
    print(f"DATABASE={database}")
    print(f"DRIVER={driver}")
    print(f"AUTH={'Windows Integrated' if trusted else 'SQL Server'}")
    print(f"ENCRYPT={values['Encrypt']}")
    print(f"TRUST_SERVER_CERTIFICATE={values['TrustServerCertificate']}")
    connection_string = ";".join(
        f"{key}={odbc_escape(str(value))}" for key, value in values.items()
    )
    try:
        connection = pyodbc.connect(connection_string, autocommit=False)
        try:
            cursor = connection.cursor()
            row = cursor.execute(
                "SELECT CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)), DB_NAME()"
            ).fetchone()
            print(f"CONNECTED_SERVER={row[0]}")
            print(f"CONNECTED_DATABASE={row[1]}")
            print("READ_ONLY_TEST=OK")
        finally:
            connection.rollback()
            connection.close()
        return 0
    except pyodbc.Error as exc:
        secret = os.getenv("DB_PASSWORD", "")
        details = " | ".join(str(item) for item in exc.args)
        if secret:
            details = details.replace(secret, "***")
        print(f"ERROR_TYPE={type(exc).__name__}")
        print(f"ERROR_ARGS={details}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

