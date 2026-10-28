-- DDL tham khao, sinh tu app/models.py. Init-db la cach khoi tao duoc huong dan.

USE [ParkingAI];
GO


CREATE TABLE areas (
	code VARCHAR(1) NOT NULL, 
	name NVARCHAR(60) NOT NULL, 
	description NVARCHAR(500) NOT NULL, 
	PRIMARY KEY (code), 
	CONSTRAINT ck_fixed_area CHECK (code IN ('A','B','D'))
)

;
GO


CREATE TABLE parking_capacity_history (
	effective_at DATETIME2(6) NOT NULL,
	capacity SMALLINT NOT NULL,
	reason VARCHAR(40) NOT NULL,
	PRIMARY KEY (effective_at),
	CONSTRAINT ck_parking_capacity_history_capacity CHECK (capacity BETWEEN 1 AND 10000),
	UNIQUE (reason)
)

;
GO


CREATE TABLE monthly_passes (
	id INTEGER NOT NULL IDENTITY, 
	code VARCHAR(32) NOT NULL, 
	plate VARCHAR(16) NOT NULL, 
	owner_name NVARCHAR(120) NOT NULL, 
	created_at DATETIME2(6) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	UNIQUE (plate)
)

;
GO


CREATE TABLE site_lock (
	id INTEGER NOT NULL IDENTITY, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id)
)

;
GO


CREATE TABLE users (
	id INTEGER NOT NULL IDENTITY, 
	username VARCHAR(64) NOT NULL, 
	full_name NVARCHAR(120) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	created_at DATETIME2(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_user_role CHECK (role IN ('ADMIN','STAFF','CUSTOMER','MANAGER')), 
	UNIQUE (username)
)

;
GO


CREATE TABLE audit_logs (
	id INTEGER NOT NULL IDENTITY, 
	actor_id INTEGER NULL, 
	action VARCHAR(60) NOT NULL, 
	detail NTEXT NOT NULL, 
	created_at DATETIME2(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
)

;
GO


CREATE TABLE backup_runs (
	id INTEGER NOT NULL IDENTITY, 
	filename NVARCHAR(255) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	created_at DATETIME2(6) NOT NULL, 
	created_by INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;
GO


CREATE TABLE pass_periods (
	id INTEGER NOT NULL IDENTITY, 
	pass_id INTEGER NOT NULL, 
	start_date DATETIME NOT NULL, 
	end_date DATETIME NOT NULL, 
	amount INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_pass_period CHECK (end_date>=start_date AND amount>=0), 
	FOREIGN KEY(pass_id) REFERENCES monthly_passes (id)
)

;
GO


CREATE TABLE rates (
	id INTEGER NOT NULL IDENTITY, 
	turn_fee INTEGER NOT NULL, 
	night_fee INTEGER NOT NULL, 
	monthly_fee INTEGER NULL, 
	created_at DATETIME2(6) NOT NULL, 
	created_by INTEGER NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_rate_nonnegative CHECK (turn_fee>=0 AND night_fee>=0 AND (monthly_fee IS NULL OR monthly_fee>=0)), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;
GO


CREATE TABLE slots (
	code VARCHAR(3) NOT NULL, 
	area_code VARCHAR(1) NOT NULL, 
	state VARCHAR(8) NOT NULL, 
	needs_review BIT NOT NULL, 
	changed_at DATETIME2(6) NOT NULL, 
	ai_state VARCHAR(8) NULL, 
	ai_confidence FLOAT NULL, 
	ai_at DATETIME2(6) NULL, 
	PRIMARY KEY (code), 
	CONSTRAINT ck_slot_state CHECK (state IN ('EMPTY','OCCUPIED')), 
	CONSTRAINT ck_fixed_slot CHECK ((area_code='A' AND code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10')) OR (area_code='B' AND code IN ('B01','B02','B03','B04','B05','B06','B07','B08','B09','B10')) OR (area_code='D' AND code IN ('D01','D02','D03','D04','D05','D06','D07','D08','D09','D10','D11','D12','D13','D14','D15','D16','D17','D18','D19','D20','D21','D22','D23','D24'))), 
	FOREIGN KEY(area_code) REFERENCES areas (code)
)

;
GO


CREATE TABLE alerts (
	id INTEGER NOT NULL IDENTITY, 
	slot_code VARCHAR(3) NOT NULL, 
	reason NVARCHAR(250) NOT NULL, 
	created_at DATETIME2(6) NOT NULL, 
	resolved_at DATETIME2(6) NULL, 
	resolved_by INTEGER NULL, 
	resolution NVARCHAR(500) NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(slot_code) REFERENCES slots (code), 
	FOREIGN KEY(resolved_by) REFERENCES users (id)
)

;
GO

CREATE UNIQUE INDEX uq_open_alert ON alerts (slot_code) WHERE resolved_at IS NULL;
GO


CREATE TABLE observations (
	id INTEGER NOT NULL IDENTITY, 
	slot_code VARCHAR(3) NOT NULL, 
	captured_at DATETIME2(6) NOT NULL, 
	state VARCHAR(8) NOT NULL, 
	confidence FLOAT NOT NULL, 
	accepted BIT NOT NULL, 
	reason NVARCHAR(250) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(slot_code) REFERENCES slots (code)
)

;
GO


CREATE TABLE visits (
	id INTEGER NOT NULL IDENTITY, 
	ticket_code VARCHAR(32) NOT NULL, 
	plate VARCHAR(16) NOT NULL, 
	slot_code VARCHAR(3) NOT NULL, 
	entered_at DATETIME2(6) NOT NULL, 
	exited_at DATETIME2(6) NULL, 
	ticket_type VARCHAR(8) NOT NULL, 
	monthly_period_id INTEGER NULL, 
	rate_id INTEGER NOT NULL, 
	created_by INTEGER NOT NULL, 
	exited_by INTEGER NULL, 
	amount INTEGER NULL, 
	fee_breakdown NTEXT NULL, 
	lost_ticket BIT NOT NULL, 
	verification_note NVARCHAR(500) NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_ticket_type CHECK (ticket_type IN ('TURN','MONTH')), 
	CONSTRAINT ck_visit_time CHECK (exited_at IS NULL OR exited_at>=entered_at), 
	CONSTRAINT ck_visit_amount CHECK (amount IS NULL OR amount>=0), 
	UNIQUE (ticket_code), 
	FOREIGN KEY(slot_code) REFERENCES slots (code), 
	FOREIGN KEY(monthly_period_id) REFERENCES pass_periods (id), 
	FOREIGN KEY(rate_id) REFERENCES rates (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(exited_by) REFERENCES users (id)
)

;
GO

CREATE INDEX ix_visit_entry ON visits (entered_at);
GO

CREATE INDEX ix_visit_exit ON visits (exited_at);
GO

CREATE UNIQUE INDEX uq_active_plate ON visits (plate) WHERE exited_at IS NULL;
GO

CREATE UNIQUE INDEX uq_active_slot ON visits (slot_code) WHERE exited_at IS NULL;
GO


CREATE TABLE payments (
	id INTEGER NOT NULL IDENTITY, 
	visit_id INTEGER NULL, 
	period_id INTEGER NULL, 
	amount INTEGER NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	paid_at DATETIME2(6) NOT NULL, 
	cashier_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_payment_amount CHECK (amount>=0), 
	CONSTRAINT ck_payment_source CHECK ((kind='TURN' AND visit_id IS NOT NULL AND period_id IS NULL) OR (kind='MONTH' AND period_id IS NOT NULL AND visit_id IS NULL)), 
	FOREIGN KEY(visit_id) REFERENCES visits (id), 
	FOREIGN KEY(period_id) REFERENCES pass_periods (id), 
	FOREIGN KEY(cashier_id) REFERENCES users (id)
)

;
GO

CREATE UNIQUE INDEX uq_payment_visit ON payments (visit_id) WHERE visit_id IS NOT NULL;
GO

CREATE UNIQUE INDEX uq_payment_period ON payments (period_id) WHERE period_id IS NOT NULL;
GO
