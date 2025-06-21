import os
import redis
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from src.python.telegram_bot.db import db

collection = db["cover-letters"]
