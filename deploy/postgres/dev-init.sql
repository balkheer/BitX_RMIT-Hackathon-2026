-- Local development only. Passwords here are throwaway and must never be used elsewhere.
CREATE ROLE cb_owner LOGIN PASSWORD 'cb_owner_local_dev';
CREATE ROLE cb_app LOGIN PASSWORD 'cb_app_local_dev';
CREATE DATABASE consentbridge OWNER cb_owner;
CREATE DATABASE consentbridge_test OWNER cb_owner;
