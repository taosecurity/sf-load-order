"""Entry script: python -I sf-load-order.py [options]. (-I leaves the script's own folder off the import path, so
it is added here.)"""
import os
import sys

if sys.version_info < (3, 9):
    print("RESULT: CANNOT CHECK: sf-load-order needs Python 3.9 or newer. Fix: install it from https://www.python.org")
    sys.exit(2)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sf_load_order.cli import main  # noqa: E402

sys.exit(main())
