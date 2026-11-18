#!/bin/sh
set -eu
cp src/job_radar/parser.py ../job_radar_rollback/parser.py
printf 'rollback copy restored to ../job_radar_rollback/parser.py\n'
