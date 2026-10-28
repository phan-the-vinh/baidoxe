/*
  DRAFT ONLY - DO NOT EXECUTE WITHOUT EXPLICIT APPROVAL.

  Target database: ParkingAI
  Approved backup:
  C:\SQLBackups\ParkingAI_FULL_COPY_ONLY_CHECKSUM_20260927_224549_399.bak

  Purpose:
    A01-A10 -> D01-D10
    B01-B10 -> D11-D20
    add D21-D24 as EMPTY

  The gate below intentionally prevents accidental execution. Change
  @ExecuteMigration to 1 only in the approved maintenance window, after
  stopping Flask and repeating the preflight queries in the test plan.
*/

/* Required by the existing filtered unique indexes on visits/alerts. */
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @ExecuteMigration bit = 0;
DECLARE @ExpectedDatabase sysname = N'ParkingAI';
DECLARE @ForceRollbackTest bit = 0;
DECLARE @CapacityEffectiveAt datetime2(6) = SYSUTCDATETIME();
DECLARE @ApprovedBackup nvarchar(260) =
    N'C:\SQLBackups\ParkingAI_FULL_COPY_ONLY_CHECKSUM_20260927_224549_399.bak';

IF @ExecuteMigration <> 1
    THROW 51000, 'DRAFT ONLY: migration execution has not been approved.', 1;

IF DB_NAME() <> @ExpectedDatabase
    THROW 51001, 'Wrong database. Connect explicitly to the approved target.', 1;

IF @CapacityEffectiveAt <= CONVERT(datetime2(6), '19000101', 112)
    THROW 51004, 'Capacity cutover timestamp is invalid.', 1;

IF NOT EXISTS (
    SELECT 1
    FROM sys.databases
    WHERE name = N'ParkingAI' AND state_desc = N'ONLINE'
)
    THROW 51002, 'ParkingAI is not ONLINE.', 1;

IF NOT EXISTS (
    SELECT 1
    FROM msdb.dbo.backupset AS bs
    JOIN msdb.dbo.backupmediafamily AS bmf
      ON bmf.media_set_id = bs.media_set_id
    WHERE bs.database_name = N'ParkingAI'
      AND bs.[type] = N'D'
      AND bs.is_copy_only = 1
      AND bs.has_backup_checksums = 1
      AND bmf.physical_device_name = @ApprovedBackup
)
    THROW 51003, 'Approved COPY_ONLY/CHECKSUM backup is not present in backup history.', 1;

DECLARE @SlotMap table (
    old_code varchar(3) NOT NULL PRIMARY KEY,
    new_code varchar(3) NOT NULL UNIQUE
);

INSERT INTO @SlotMap (old_code, new_code) VALUES
    ('A01','D01'),('A02','D02'),('A03','D03'),('A04','D04'),('A05','D05'),
    ('A06','D06'),('A07','D07'),('A08','D08'),('A09','D09'),('A10','D10'),
    ('B01','D11'),('B02','D12'),('B03','D13'),('B04','D14'),('B05','D15'),
    ('B06','D16'),('B07','D17'),('B08','D18'),('B09','D19'),('B10','D20');

BEGIN TRY
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    BEGIN TRANSACTION;

    /* Lock only the migration scope. Flask must also be stopped. */
    DECLARE
        @AreaCountBefore int,
        @SlotCountBefore int,
        @VisitCountBefore int,
        @ObservationCountBefore int,
        @AlertCountBefore int,
        @PaymentCountBefore int,
        @AuditCountBefore int,
        @ActiveB02Before int,
        @VisitBusinessChecksumBefore int,
        @ObservationBusinessChecksumBefore int,
        @AlertBusinessChecksumBefore int,
        @PaymentChecksumBefore int,
        @AuditChecksumBefore int;

    SELECT @AreaCountBefore = COUNT(*) FROM dbo.areas WITH (TABLOCKX, HOLDLOCK);
    SELECT @SlotCountBefore = COUNT(*) FROM dbo.slots WITH (TABLOCKX, HOLDLOCK);
    SELECT @VisitCountBefore = COUNT(*) FROM dbo.visits WITH (TABLOCKX, HOLDLOCK);
    SELECT @ObservationCountBefore = COUNT(*) FROM dbo.observations WITH (TABLOCKX, HOLDLOCK);
    SELECT @AlertCountBefore = COUNT(*) FROM dbo.alerts WITH (TABLOCKX, HOLDLOCK);
    SELECT @PaymentCountBefore = COUNT(*) FROM dbo.payments WITH (TABLOCKX, HOLDLOCK);
    SELECT @AuditCountBefore = COUNT(*) FROM dbo.audit_logs WITH (TABLOCKX, HOLDLOCK);

    IF @AreaCountBefore <> 2
       OR EXISTS (SELECT code FROM dbo.areas EXCEPT SELECT code FROM (VALUES ('A'),('B')) AS x(code))
       OR EXISTS (SELECT code FROM (VALUES ('A'),('B')) AS x(code) EXCEPT SELECT code FROM dbo.areas)
        THROW 51010, 'Preflight failed: areas must be exactly A and B.', 1;

    IF @SlotCountBefore <> 20
       OR EXISTS (SELECT code FROM dbo.slots EXCEPT SELECT old_code FROM @SlotMap)
       OR EXISTS (SELECT old_code FROM @SlotMap EXCEPT SELECT code FROM dbo.slots)
        THROW 51011, 'Preflight failed: slots must be exactly A01-A10 and B01-B10.', 1;

    IF EXISTS (SELECT 1 FROM dbo.slots WHERE area_code NOT IN ('A','B'))
        THROW 51012, 'Preflight failed: unexpected slot area.', 1;

    IF EXISTS (
        SELECT new_code FROM @SlotMap
        INTERSECT
        SELECT code FROM dbo.slots
    )
        THROW 51013, 'Preflight failed: a target D code already exists.', 1;

    IF EXISTS (SELECT 1 FROM dbo.visits WHERE slot_code NOT IN (SELECT old_code FROM @SlotMap))
       OR EXISTS (SELECT 1 FROM dbo.observations WHERE slot_code NOT IN (SELECT old_code FROM @SlotMap))
       OR EXISTS (SELECT 1 FROM dbo.alerts WHERE slot_code NOT IN (SELECT old_code FROM @SlotMap))
        THROW 51014, 'Preflight failed: a child row contains an unmapped slot code.', 1;

    IF OBJECT_ID(N'dbo.ck_fixed_area', N'C') IS NULL
       OR OBJECT_ID(N'dbo.ck_fixed_slot', N'C') IS NULL
       OR OBJECT_ID(N'dbo.FK__slots__area_code__45F365D3', N'F') IS NULL
       OR OBJECT_ID(N'dbo.FK__visits__slot_cod__59FA5E80', N'F') IS NULL
       OR OBJECT_ID(N'dbo.FK__observati__slot___60A75C0F', N'F') IS NULL
       OR OBJECT_ID(N'dbo.FK__alerts__slot_cod__6383C8BA', N'F') IS NULL
        THROW 51015, 'Preflight failed: schema constraint names differ from the inspected database.', 1;

    IF OBJECT_ID(N'dbo.parking_capacity_history', N'U') IS NOT NULL
        THROW 51018, 'Preflight failed: parking_capacity_history already exists.', 1;

    IF EXISTS (
        SELECT slot_code
        FROM dbo.visits
        WHERE exited_at IS NULL
        GROUP BY slot_code
        HAVING COUNT(*) > 1
    )
        THROW 51016, 'Preflight failed: duplicate active visits for a slot.', 1;

    IF EXISTS (
        SELECT 1
        FROM dbo.visits AS v
        LEFT JOIN dbo.slots AS s ON s.code = v.slot_code
        WHERE s.code IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM dbo.observations AS o
        LEFT JOIN dbo.slots AS s ON s.code = o.slot_code
        WHERE s.code IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM dbo.alerts AS a
        LEFT JOIN dbo.slots AS s ON s.code = a.slot_code
        WHERE s.code IS NULL
    )
        THROW 51017, 'Preflight failed: orphan slot reference detected.', 1;

    SELECT @ActiveB02Before = COUNT(*)
    FROM dbo.visits
    WHERE slot_code = 'B02' AND exited_at IS NULL;

    SELECT @VisitBusinessChecksumBefore = CHECKSUM_AGG(BINARY_CHECKSUM(
        id, ticket_code, plate, entered_at, exited_at, ticket_type,
        monthly_period_id, rate_id, created_by, exited_by, amount,
        CONVERT(nvarchar(4000), fee_breakdown), lost_ticket, verification_note
    )) FROM dbo.visits;

    SELECT @ObservationBusinessChecksumBefore = CHECKSUM_AGG(BINARY_CHECKSUM(
        id, captured_at, state, confidence, accepted, reason
    )) FROM dbo.observations;

    SELECT @AlertBusinessChecksumBefore = CHECKSUM_AGG(BINARY_CHECKSUM(
        id, reason, created_at, resolved_at, resolved_by, resolution
    )) FROM dbo.alerts;

    SELECT @PaymentChecksumBefore = CHECKSUM_AGG(BINARY_CHECKSUM(
        id, visit_id, period_id, amount, kind, paid_at, cashier_id
    )) FROM dbo.payments;

    SELECT @AuditChecksumBefore = CHECKSUM_AGG(BINARY_CHECKSUM(
        id, actor_id, action, CONVERT(nvarchar(4000), detail), created_at
    )) FROM dbo.audit_logs;

    SELECT code AS old_code, state, needs_review, changed_at,
           ai_state, ai_confidence, ai_at
    INTO #SlotBefore
    FROM dbo.slots;

    SELECT * INTO #VisitBefore FROM dbo.visits;
    SELECT * INTO #ObservationBefore FROM dbo.observations;
    SELECT * INTO #AlertBefore FROM dbo.alerts;
    SELECT * INTO #PaymentBefore FROM dbo.payments;
    SELECT * INTO #AuditBefore FROM dbo.audit_logs;

    ALTER TABLE dbo.alerts DROP CONSTRAINT [FK__alerts__slot_cod__6383C8BA];
    ALTER TABLE dbo.observations DROP CONSTRAINT [FK__observati__slot___60A75C0F];
    ALTER TABLE dbo.visits DROP CONSTRAINT [FK__visits__slot_cod__59FA5E80];
    ALTER TABLE dbo.slots DROP CONSTRAINT [FK__slots__area_code__45F365D3];
    ALTER TABLE dbo.slots DROP CONSTRAINT [ck_fixed_slot];
    ALTER TABLE dbo.areas DROP CONSTRAINT [ck_fixed_area];

    INSERT INTO dbo.areas (code, name, description)
    VALUES ('D', N'Khu D', N'24 vị trí cố định, từ D01 đến D24.');

    UPDATE v
       SET v.slot_code = m.new_code
    FROM dbo.visits AS v
    JOIN @SlotMap AS m ON m.old_code = v.slot_code;

    UPDATE o
       SET o.slot_code = m.new_code
    FROM dbo.observations AS o
    JOIN @SlotMap AS m ON m.old_code = o.slot_code;

    UPDATE a
       SET a.slot_code = m.new_code
    FROM dbo.alerts AS a
    JOIN @SlotMap AS m ON m.old_code = a.slot_code;

    UPDATE s
       SET s.code = m.new_code,
           s.area_code = 'D'
    FROM dbo.slots AS s
    JOIN @SlotMap AS m ON m.old_code = s.code;

    INSERT INTO dbo.slots
        (code, area_code, state, needs_review, changed_at,
         ai_state, ai_confidence, ai_at)
    VALUES
        ('D21','D','EMPTY',0,SYSUTCDATETIME(),NULL,NULL,NULL),
        ('D22','D','EMPTY',0,SYSUTCDATETIME(),NULL,NULL,NULL),
        ('D23','D','EMPTY',0,SYSUTCDATETIME(),NULL,NULL,NULL),
        ('D24','D','EMPTY',0,SYSUTCDATETIME(),NULL,NULL,NULL);

    CREATE TABLE dbo.parking_capacity_history (
        effective_at datetime2(6) NOT NULL,
        capacity smallint NOT NULL,
        reason varchar(40) NOT NULL,
        CONSTRAINT PK_parking_capacity_history PRIMARY KEY (effective_at),
        CONSTRAINT UQ_parking_capacity_history_reason UNIQUE (reason),
        CONSTRAINT CK_parking_capacity_history_capacity
            CHECK (capacity BETWEEN 1 AND 10000)
    );

    INSERT INTO dbo.parking_capacity_history (effective_at, capacity, reason)
    VALUES
        (CONVERT(datetime2(6), '19000101', 112), 20, 'BASELINE_20'),
        (@CapacityEffectiveAt, 24, 'MIGRATION_D24');

    DELETE FROM dbo.areas WHERE code IN ('A','B');

    ALTER TABLE dbo.areas WITH CHECK ADD CONSTRAINT ck_fixed_area
        CHECK (code = 'D');

    ALTER TABLE dbo.slots WITH CHECK ADD CONSTRAINT ck_fixed_slot
        CHECK (area_code = 'D' AND code IN (
            'D01','D02','D03','D04','D05','D06','D07','D08',
            'D09','D10','D11','D12','D13','D14','D15','D16',
            'D17','D18','D19','D20','D21','D22','D23','D24'
        ));

    ALTER TABLE dbo.slots WITH CHECK ADD CONSTRAINT FK_slots_areas_area_code
        FOREIGN KEY (area_code) REFERENCES dbo.areas(code);
    ALTER TABLE dbo.visits WITH CHECK ADD CONSTRAINT FK_visits_slots_slot_code
        FOREIGN KEY (slot_code) REFERENCES dbo.slots(code);
    ALTER TABLE dbo.observations WITH CHECK ADD CONSTRAINT FK_observations_slots_slot_code
        FOREIGN KEY (slot_code) REFERENCES dbo.slots(code);
    ALTER TABLE dbo.alerts WITH CHECK ADD CONSTRAINT FK_alerts_slots_slot_code
        FOREIGN KEY (slot_code) REFERENCES dbo.slots(code);

    ALTER TABLE dbo.slots CHECK CONSTRAINT FK_slots_areas_area_code;
    ALTER TABLE dbo.visits CHECK CONSTRAINT FK_visits_slots_slot_code;
    ALTER TABLE dbo.observations CHECK CONSTRAINT FK_observations_slots_slot_code;
    ALTER TABLE dbo.alerts CHECK CONSTRAINT FK_alerts_slots_slot_code;

    IF (SELECT COUNT(*) FROM dbo.areas) <> 1
       OR NOT EXISTS (SELECT 1 FROM dbo.areas WHERE code = 'D')
        THROW 51030, 'Postflight failed: expected exactly area D.', 1;

    IF (SELECT COUNT(*) FROM dbo.slots) <> 24
       OR EXISTS (SELECT code FROM dbo.slots EXCEPT SELECT code FROM (VALUES
            ('D01'),('D02'),('D03'),('D04'),('D05'),('D06'),('D07'),('D08'),
            ('D09'),('D10'),('D11'),('D12'),('D13'),('D14'),('D15'),('D16'),
            ('D17'),('D18'),('D19'),('D20'),('D21'),('D22'),('D23'),('D24')
       ) AS x(code))
        THROW 51031, 'Postflight failed: expected exactly D01-D24.', 1;

    IF EXISTS (SELECT 1 FROM dbo.slots WHERE area_code <> 'D')
        THROW 51032, 'Postflight failed: a slot is outside area D.', 1;

    IF (SELECT COUNT(*) FROM dbo.visits) <> @VisitCountBefore
       OR (SELECT COUNT(*) FROM dbo.observations) <> @ObservationCountBefore
       OR (SELECT COUNT(*) FROM dbo.alerts) <> @AlertCountBefore
       OR (SELECT COUNT(*) FROM dbo.payments) <> @PaymentCountBefore
       OR (SELECT COUNT(*) FROM dbo.audit_logs) <> @AuditCountBefore
        THROW 51033, 'Postflight failed: a protected table row count changed.', 1;

    IF ISNULL((SELECT CHECKSUM_AGG(BINARY_CHECKSUM(
        id, ticket_code, plate, entered_at, exited_at, ticket_type,
        monthly_period_id, rate_id, created_by, exited_by, amount,
        CONVERT(nvarchar(4000), fee_breakdown), lost_ticket, verification_note
    )) FROM dbo.visits), 0) <> ISNULL(@VisitBusinessChecksumBefore, 0)
        THROW 51034, 'Postflight failed: non-slot visit data changed.', 1;

    IF ISNULL((SELECT CHECKSUM_AGG(BINARY_CHECKSUM(
        id, captured_at, state, confidence, accepted, reason
    )) FROM dbo.observations), 0) <> ISNULL(@ObservationBusinessChecksumBefore, 0)
        THROW 51035, 'Postflight failed: non-slot observation data changed.', 1;

    IF ISNULL((SELECT CHECKSUM_AGG(BINARY_CHECKSUM(
        id, reason, created_at, resolved_at, resolved_by, resolution
    )) FROM dbo.alerts), 0) <> ISNULL(@AlertBusinessChecksumBefore, 0)
        THROW 51036, 'Postflight failed: non-slot alert data changed.', 1;

    IF ISNULL((SELECT CHECKSUM_AGG(BINARY_CHECKSUM(
        id, visit_id, period_id, amount, kind, paid_at, cashier_id
    )) FROM dbo.payments), 0) <> ISNULL(@PaymentChecksumBefore, 0)
        THROW 51037, 'Postflight failed: payment data changed.', 1;

    IF ISNULL((SELECT CHECKSUM_AGG(BINARY_CHECKSUM(
        id, actor_id, action, CONVERT(nvarchar(4000), detail), created_at
    )) FROM dbo.audit_logs), 0) <> ISNULL(@AuditChecksumBefore, 0)
        THROW 51038, 'Postflight failed: audit log data changed.', 1;

    IF EXISTS (
        SELECT b.id, b.ticket_code, b.plate, m.new_code, b.entered_at,
               b.exited_at, b.ticket_type, b.monthly_period_id, b.rate_id,
               b.created_by, b.exited_by, b.amount, b.fee_breakdown,
               b.lost_ticket, b.verification_note
        FROM #VisitBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
        EXCEPT
        SELECT id, ticket_code, plate, slot_code, entered_at,
               exited_at, ticket_type, monthly_period_id, rate_id,
               created_by, exited_by, amount, fee_breakdown,
               lost_ticket, verification_note
        FROM dbo.visits
    ) OR EXISTS (
        SELECT id, ticket_code, plate, slot_code, entered_at,
               exited_at, ticket_type, monthly_period_id, rate_id,
               created_by, exited_by, amount, fee_breakdown,
               lost_ticket, verification_note
        FROM dbo.visits
        EXCEPT
        SELECT b.id, b.ticket_code, b.plate, m.new_code, b.entered_at,
               b.exited_at, b.ticket_type, b.monthly_period_id, b.rate_id,
               b.created_by, b.exited_by, b.amount, b.fee_breakdown,
               b.lost_ticket, b.verification_note
        FROM #VisitBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
    )
        THROW 51039, 'Postflight failed: visit rows differ from the exact mapped expectation.', 1;

    IF EXISTS (
        SELECT b.id, m.new_code, b.captured_at, b.state, b.confidence,
               b.accepted, b.reason
        FROM #ObservationBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
        EXCEPT
        SELECT id, slot_code, captured_at, state, confidence, accepted, reason
        FROM dbo.observations
    ) OR EXISTS (
        SELECT id, slot_code, captured_at, state, confidence, accepted, reason
        FROM dbo.observations
        EXCEPT
        SELECT b.id, m.new_code, b.captured_at, b.state, b.confidence,
               b.accepted, b.reason
        FROM #ObservationBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
    )
        THROW 51043, 'Postflight failed: observation rows differ from the exact mapped expectation.', 1;

    IF EXISTS (
        SELECT b.id, m.new_code, b.reason, b.created_at, b.resolved_at,
               b.resolved_by, b.resolution
        FROM #AlertBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
        EXCEPT
        SELECT id, slot_code, reason, created_at, resolved_at,
               resolved_by, resolution
        FROM dbo.alerts
    ) OR EXISTS (
        SELECT id, slot_code, reason, created_at, resolved_at,
               resolved_by, resolution
        FROM dbo.alerts
        EXCEPT
        SELECT b.id, m.new_code, b.reason, b.created_at, b.resolved_at,
               b.resolved_by, b.resolution
        FROM #AlertBefore AS b JOIN @SlotMap AS m ON m.old_code = b.slot_code
    )
        THROW 51044, 'Postflight failed: alert rows differ from the exact mapped expectation.', 1;

    IF EXISTS (SELECT * FROM #PaymentBefore EXCEPT SELECT * FROM dbo.payments)
       OR EXISTS (SELECT * FROM dbo.payments EXCEPT SELECT * FROM #PaymentBefore)
        THROW 51045, 'Postflight failed: payment rows changed.', 1;

    IF EXISTS (SELECT * FROM #AuditBefore EXCEPT SELECT * FROM dbo.audit_logs)
       OR EXISTS (SELECT * FROM dbo.audit_logs EXCEPT SELECT * FROM #AuditBefore)
        THROW 51046, 'Postflight failed: audit rows changed.', 1;

    IF EXISTS (
        SELECT m.new_code, b.state, b.needs_review, b.changed_at,
               b.ai_state, b.ai_confidence, b.ai_at
        FROM #SlotBefore AS b JOIN @SlotMap AS m ON m.old_code = b.old_code
        EXCEPT
        SELECT code, state, needs_review, changed_at,
               ai_state, ai_confidence, ai_at
        FROM dbo.slots WHERE code BETWEEN 'D01' AND 'D20'
    ) OR EXISTS (
        SELECT code, state, needs_review, changed_at,
               ai_state, ai_confidence, ai_at
        FROM dbo.slots WHERE code BETWEEN 'D01' AND 'D20'
        EXCEPT
        SELECT m.new_code, b.state, b.needs_review, b.changed_at,
               b.ai_state, b.ai_confidence, b.ai_at
        FROM #SlotBefore AS b JOIN @SlotMap AS m ON m.old_code = b.old_code
    )
        THROW 51040, 'Postflight failed: an existing slot state changed.', 1;

    IF (SELECT COUNT(*) FROM dbo.visits WHERE slot_code = 'D12' AND exited_at IS NULL)
       <> @ActiveB02Before
        THROW 51041, 'Postflight failed: active B02 visit was not preserved as D12.', 1;

    IF EXISTS (
        SELECT 1 FROM dbo.visits AS v LEFT JOIN dbo.slots AS s ON s.code = v.slot_code
        WHERE s.code IS NULL
    ) OR EXISTS (
        SELECT 1 FROM dbo.observations AS o LEFT JOIN dbo.slots AS s ON s.code = o.slot_code
        WHERE s.code IS NULL
    ) OR EXISTS (
        SELECT 1 FROM dbo.alerts AS a LEFT JOIN dbo.slots AS s ON s.code = a.slot_code
        WHERE s.code IS NULL
    )
        THROW 51042, 'Postflight failed: orphan slot reference detected.', 1;

    IF (SELECT COUNT(*) FROM dbo.parking_capacity_history) <> 2
       OR NOT EXISTS (
            SELECT 1 FROM dbo.parking_capacity_history
            WHERE effective_at = CONVERT(datetime2(6), '19000101', 112)
              AND capacity = 20 AND reason = 'BASELINE_20'
       )
       OR NOT EXISTS (
            SELECT 1 FROM dbo.parking_capacity_history
            WHERE effective_at = @CapacityEffectiveAt
              AND capacity = 24 AND reason = 'MIGRATION_D24'
       )
        THROW 51047, 'Postflight failed: capacity history is invalid.', 1;

    IF @ForceRollbackTest = 1
        THROW 51090, 'Forced rollback test before COMMIT.', 1;

    COMMIT TRANSACTION;

    SELECT N'MIGRATION_SUCCEEDED' AS result,
           (SELECT COUNT(*) FROM dbo.slots) AS slots,
           (SELECT COUNT(*) FROM dbo.visits) AS visits,
           (SELECT COUNT(*) FROM dbo.payments) AS payments,
           (SELECT COUNT(*) FROM dbo.audit_logs) AS audit_logs;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
