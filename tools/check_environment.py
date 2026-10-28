import argparse
import importlib.util
import sys
import os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',action='store_true');args=parser.parse_args()
    print('Python:',sys.version.split()[0])
    for module in ['flask','flask_sqlalchemy','flask_wtf','pyodbc','cv2','numpy','pytesseract','tensorflow','google.genai']:
        try:found=importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:found=False
        print(f'{module}:', 'OK' if found else 'CHUA CAI')
    try:
        import pyodbc
        print('ODBC drivers:',', '.join(pyodbc.drivers()) or 'Chưa có SQL Server ODBC Driver')
    except ImportError:pass
    if args.database:
        try:
            from app import create_app
            from app.extensions import db
            from sqlalchemy import text
            application=create_app()
            with application.app_context():
                db.session.execute(text('SELECT 1')).scalar_one()
                print('SQL Server: kết nối thành công.')
                print('Số vị trí:',db.session.execute(text('SELECT COUNT(*) FROM slots')).scalar_one())
        except Exception as exc:
            print('SQL Server: chưa kiểm tra thành công ('+type(exc).__name__+').')
            print('Kiểm tra .env, dịch vụ SQL Server, ODBC Driver, database và chạy init-db. Không in chuỗi kết nối để bảo vệ thông tin xác thực.')
            raise SystemExit(1)

if __name__=='__main__':main()
