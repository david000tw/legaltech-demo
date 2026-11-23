-- 應用程式連線角色：不能繞過 row-level security，也不擁有任何資料表。
CREATE ROLE app_user LOGIN PASSWORD 'apppass' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE legaltech;
CREATE DATABASE legaltech_test;
