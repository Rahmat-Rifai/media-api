#!/bin/bash
set -euo pipefail

mkdir -p /home/hatch/media-api/data
mkdir -p /home/hatch/media-api/storage/tmp
mkdir -p /home/hatch/media-api/storage/files
mkdir -p /home/mediaapi/.cache

chown -R mediaapi:mediaapi /home/hatch/media-api/data
chown -R mediaapi:mediaapi /home/hatch/media-api/storage
chown -R mediaapi:mediaapi /home/mediaapi/.cache

cp deploy/media-api.service /etc/systemd/system/media-api.service
systemctl daemon-reload
systemctl enable media-api.service
systemctl restart media-api.service
systemctl status media-api.service --no-pager

