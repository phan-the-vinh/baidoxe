import getpass
import re
import secrets
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    target=root/'.env'
    if target.exists():
        print('.env đã tồn tại; không ghi đè. Mở file để cập nhật cấu hình nếu cần.');return
    username=input('Tên tài khoản Quản lý [admin]: ').strip().lower() or 'admin'
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}',username):raise SystemExit('Tên đăng nhập không hợp lệ.')
    password=getpass.getpass('Mật khẩu Quản lý (10–128 ký tự, không hiển thị): ')
    if not 10<=len(password)<=128 or '\n' in password or '\r' in password:raise SystemExit('Mật khẩu không hợp lệ.')
    if getpass.getpass('Nhập lại mật khẩu: ')!=password:raise SystemExit('Mật khẩu nhập lại không khớp.')
    def quote(s):return "'"+s.replace('\\','\\\\').replace("'","\\'")+"'"
    content=(root/'.env.example').read_text(encoding='utf-8')
    content=re.sub(r'^SECRET_KEY=.*$',f'SECRET_KEY={secrets.token_hex(32)}',content,flags=re.M)
    content=re.sub(r'^ADMIN_USERNAME=.*$',lambda m:'ADMIN_USERNAME='+username,content,flags=re.M)
    content=re.sub(r'^ADMIN_PASSWORD=.*$',lambda m:'ADMIN_PASSWORD='+quote(password),content,flags=re.M)
    target.write_text(content,encoding='utf-8')
    print('Đã tạo .env. Kiểm tra DB_SERVER và cấu hình SQL Server trước khi khởi tạo CSDL.')

if __name__=='__main__':main()
