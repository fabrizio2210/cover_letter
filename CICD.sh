#!/bin/bash -x


if [ $ACTION = "insert_email" ]; then
    python3 src/python/email_extractor/insert_email.py --db_uri ${DB_URI} --db_name cover_letter --email ${EMAIL_TO_INSERT}
fi