import pytest
from app import create_app
from app.extensions import db
from app.cli import seed_database
from app.models import User

@pytest.fixture
def app(tmp_path):
    application = create_app({
        'TESTING': True,
        'SECRET_KEY': 'test-session-secret-not-for-deployment',
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(tmp_path / 'test.db'),
        'WTF_CSRF_ENABLED': False
    })
    with application.app_context():
        db.create_all()
        # User id 1: username='manager', role='ADMIN'
        seed_database('manager', 'test-manager-only', full_name='Quản trị viên')
        # User id 2: legacy manager with role='MANAGER'
        legacy_mgr = User(username='legacy_manager', full_name='Quản lý cũ', role='MANAGER')
        legacy_mgr.set_password('test-legacy-only')
        # User id 3: staff with role='STAFF'
        employee = User(username='employee', full_name='Nhân viên kiểm thử', role='STAFF')
        employee.set_password('test-employee-only')
        # User id 4: customer with role='CUSTOMER'
        customer = User(username='customer', full_name='Khách hàng kiểm thử', role='CUSTOMER')
        customer.set_password('test-customer-only')
        db.session.add_all([legacy_mgr, employee, customer])
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def admin(app):
    c = app.test_client()
    assert c.post('/login', data={'username': 'manager', 'password': 'test-manager-only'}).status_code == 302
    return c

@pytest.fixture
def manager(app):
    c = app.test_client()
    assert c.post('/login', data={'username': 'manager', 'password': 'test-manager-only'}).status_code == 302
    return c

@pytest.fixture
def legacy_manager(app):
    c = app.test_client()
    assert c.post('/login', data={'username': 'legacy_manager', 'password': 'test-legacy-only'}).status_code == 302
    return c

@pytest.fixture
def staff(app):
    c = app.test_client()
    assert c.post('/login', data={'username': 'employee', 'password': 'test-employee-only'}).status_code == 302
    return c

@pytest.fixture
def customer(app):
    c = app.test_client()
    assert c.post('/login', data={'username': 'customer', 'password': 'test-customer-only'}).status_code == 302
    return c
