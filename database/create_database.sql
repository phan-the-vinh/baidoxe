-- Chay trong SQL Server Management Studio. Khong chua tai khoan/mat khau.
-- DB_NAME trong .env phai khop ParkingAI, hoac doi ten ben duoi theo .env.
USE [master];
GO
IF DB_ID(N'ParkingAI') IS NULL
BEGIN
    CREATE DATABASE [ParkingAI];
END;
GO
-- Sau do tu thu muc du an chay:
-- python -m flask --app app:create_app init-db
-- Lenh nay tao bang, 20 o, bang gia demo va tai khoan Quan ly tu .env.
