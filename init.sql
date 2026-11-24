-- 應用程式連線角色：不能繞過 row-level security，也不擁有任何資料表。
CREATE ROLE app_user LOGIN PASSWORD 'apppass' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
-- 律師貼司法院網址加入判決用：只能讀、新增判決與段落（權限在 migration 0009 授予）。
CREATE ROLE corpus_writer LOGIN PASSWORD 'corpuspass' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE legaltech;
CREATE DATABASE legaltech_test;
