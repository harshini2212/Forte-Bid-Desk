"""Launch the Forte Bid Desk without setting PYTHONPATH:

    python serve.py            # -> http://localhost:8000
    python serve.py --port 8050
    python serve.py --precompute   # cache estimates + routing metrics, then exit
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)                       # data/ and .cache/ resolve relative to the project
sys.path.insert(0, os.path.join(_HERE, "src"))

from forte.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
