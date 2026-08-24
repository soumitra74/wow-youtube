@echo off
cd /d "%~dp0.."
docker compose exec -T wow python -m wow_poller %*
