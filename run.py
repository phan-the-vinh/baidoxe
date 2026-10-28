"""Chay local bang Waitress; khong mo debugger hoac cong ra mang LAN."""
import os
from app import create_app

if __name__ == '__main__':
    from waitress import serve
    application = create_app()
    port = int(os.getenv('PORT', '5000'))
    print(f'Mo trinh duyet: http://127.0.0.1:{port}')
    serve(application, host='127.0.0.1', port=port, threads=6)