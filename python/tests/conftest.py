"""Shared test setup.

Auth enforcement is off for the suite: endpoint tests exercise handlers, not
logins. tests/test_atlas_auth.py turns it back on for its own cases.
"""
import os

os.environ.setdefault('ATLAS_AUTH', 'off')
