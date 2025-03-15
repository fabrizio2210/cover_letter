#!/bin/bash -x

if [ -z "$ACTION" ]; then
    echo "ACTION is not set"
    ACTION=""
fi

if [ $ACTION = "insert_email" ]; then
    python3 src/python/email_extractor/insert_email.py --db_uri ${DB_URI} --db_name cover_letter --email ${EMAIL_TO_INSERT}
fi