import os
from pathlib import Path
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]

def odbc_value(value):
    return '{' + str(value).replace('}', '}}') + '}'

def database_url():
    values = {
        'DRIVER': os.getenv('DB_DRIVER', 'ODBC Driver 18 for SQL Server'),
        'SERVER': os.getenv('DB_SERVER', r'localhost\SQLEXPRESS'),
        'DATABASE': os.getenv('DB_NAME', 'ParkingAI'),
        'Encrypt': os.getenv('DB_ENCRYPT', 'yes'),
        'TrustServerCertificate': os.getenv('DB_TRUST_SERVER_CERTIFICATE', 'yes'),
    }
    if os.getenv('DB_TRUSTED_CONNECTION', 'true').lower() == 'true':
        values['Trusted_Connection'] = 'yes'
    else:
        values['UID'] = os.getenv('DB_USER', '')
        values['PWD'] = os.getenv('DB_PASSWORD', '')
        if not values['UID'] or not values['PWD']:
            raise RuntimeError('Hãy điền DB_USER và DB_PASSWORD trong .env.')
    values['Connection Timeout'] = '8'
    return URL.create('mssql+pyodbc', query={
        'odbc_connect': ';'.join(f'{key}={odbc_value(value)}' for key, value in values.items())
    })

def project_path(key, default):
    path = Path(os.getenv(key) or default)
    return path if path.is_absolute() else ROOT / path
