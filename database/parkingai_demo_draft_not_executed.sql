/*
  BAN NHAP - CHUA DUOC THUC THI.
  Database nay tach biet hoan toan voi ParkingAI nghiep vu.
*/
USE [master];
GO

IF DB_ID(N'ParkingAI_Demo') IS NULL
BEGIN
    CREATE DATABASE [ParkingAI_Demo];
END;
GO

USE [ParkingAI_Demo];
GO

CREATE TABLE dbo.demo_videos (
    video_id BIGINT IDENTITY(1,1) NOT NULL,
    source_key VARCHAR(100) NOT NULL,
    source_video_name NVARCHAR(260) NOT NULL,
    processed_video_name NVARCHAR(260) NOT NULL,
    pipeline_run_dir NVARCHAR(500) NOT NULL,
    source_width INT NOT NULL,
    source_height INT NOT NULL,
    output_width INT NOT NULL,
    output_height INT NOT NULL,
    fps DECIMAL(10,6) NOT NULL,
    frame_count INT NOT NULL,
    duration_seconds DECIMAL(12,6) NOT NULL,
    slot_count SMALLINT NOT NULL,
    states_csv_sha256 CHAR(64) NOT NULL,
    transitions_csv_sha256 CHAR(64) NOT NULL,
    processed_video_sha256 CHAR(64) NOT NULL,
    pipeline_config_json NVARCHAR(MAX) NOT NULL,
    imported_at_utc DATETIME2(6) NOT NULL
        CONSTRAINT DF_demo_videos_imported_at DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_demo_videos PRIMARY KEY (video_id),
    CONSTRAINT UQ_demo_videos_source_key UNIQUE (source_key),
    CONSTRAINT CK_demo_videos_dimensions CHECK (
        source_width > 0 AND source_height > 0
        AND output_width > 0 AND output_height > 0
    ),
    CONSTRAINT CK_demo_videos_timing CHECK (
        fps > 0 AND frame_count > 0 AND duration_seconds > 0
    ),
    CONSTRAINT CK_demo_videos_slot_count CHECK (slot_count = 24),
    CONSTRAINT CK_demo_videos_config_json CHECK (ISJSON(pipeline_config_json) = 1)
);
GO

CREATE TABLE dbo.demo_slot_states (
    video_id BIGINT NOT NULL,
    frame_index INT NOT NULL,
    timestamp_seconds DECIMAL(12,6) NOT NULL,
    slot_code VARCHAR(3) NOT NULL,
    state VARCHAR(10) NOT NULL,
    CONSTRAINT PK_demo_slot_states
        PRIMARY KEY (video_id, frame_index, slot_code),
    CONSTRAINT FK_demo_slot_states_video
        FOREIGN KEY (video_id) REFERENCES dbo.demo_videos(video_id),
    CONSTRAINT UQ_demo_slot_states_transition_target
        UNIQUE (video_id, frame_index, slot_code, timestamp_seconds, state),
    CONSTRAINT CK_demo_slot_states_frame CHECK (frame_index >= 0),
    CONSTRAINT CK_demo_slot_states_timestamp CHECK (timestamp_seconds >= 0),
    CONSTRAINT CK_demo_slot_states_slot CHECK (slot_code IN (
        'D01','D02','D03','D04','D05','D06','D07','D08',
        'D09','D10','D11','D12','D13','D14','D15','D16',
        'D17','D18','D19','D20','D21','D22','D23','D24'
    )),
    CONSTRAINT CK_demo_slot_states_state CHECK (
        state IN ('EMPTY','OCCUPIED','UNKNOWN')
    )
);
GO

CREATE INDEX IX_demo_slot_states_video_time
    ON dbo.demo_slot_states(video_id, timestamp_seconds)
    INCLUDE (frame_index, slot_code, state);
GO

CREATE INDEX IX_demo_slot_states_video_slot_time
    ON dbo.demo_slot_states(video_id, slot_code, timestamp_seconds)
    INCLUDE (frame_index, state);
GO

CREATE TABLE dbo.demo_slot_transitions (
    video_id BIGINT NOT NULL,
    frame_index INT NOT NULL,
    timestamp_seconds DECIMAL(12,6) NOT NULL,
    slot_code VARCHAR(3) NOT NULL,
    previous_state VARCHAR(10) NOT NULL,
    new_state VARCHAR(10) NOT NULL,
    evidence VARCHAR(16) NOT NULL,
    CONSTRAINT PK_demo_slot_transitions
        PRIMARY KEY (video_id, frame_index, slot_code),
    CONSTRAINT FK_demo_slot_transitions_video
        FOREIGN KEY (video_id) REFERENCES dbo.demo_videos(video_id),
    CONSTRAINT FK_demo_slot_transitions_target_state
        FOREIGN KEY (video_id, frame_index, slot_code, timestamp_seconds, new_state)
        REFERENCES dbo.demo_slot_states(
            video_id, frame_index, slot_code, timestamp_seconds, state
        ),
    CONSTRAINT CK_demo_slot_transitions_frame CHECK (frame_index >= 0),
    CONSTRAINT CK_demo_slot_transitions_timestamp CHECK (timestamp_seconds >= 0),
    CONSTRAINT CK_demo_slot_transitions_slot CHECK (slot_code IN (
        'D01','D02','D03','D04','D05','D06','D07','D08',
        'D09','D10','D11','D12','D13','D14','D15','D16',
        'D17','D18','D19','D20','D21','D22','D23','D24'
    )),
    CONSTRAINT CK_demo_slot_transitions_previous CHECK (
        previous_state IN ('EMPTY','OCCUPIED','UNKNOWN')
    ),
    CONSTRAINT CK_demo_slot_transitions_new CHECK (
        new_state IN ('EMPTY','OCCUPIED','UNKNOWN')
    ),
    CONSTRAINT CK_demo_slot_transitions_changed CHECK (previous_state <> new_state),
    CONSTRAINT CK_demo_slot_transitions_evidence CHECK (
        evidence IN ('occupied','clear','uncertain')
    )
);
GO

CREATE INDEX IX_demo_slot_transitions_video_slot_time
    ON dbo.demo_slot_transitions(video_id, slot_code, timestamp_seconds)
    INCLUDE (frame_index, previous_state, new_state, evidence);
GO
