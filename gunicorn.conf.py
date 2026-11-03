"""Defaults also used by Render services started with `gunicorn server:app`."""
import os

bind = '0.0.0.0:' + os.environ.get('PORT', '8888')
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 120
accesslog = '-'
errorlog = '-'
