#!/usr/bin/env python
import sys
from os import path

directory, _ = path.split(__file__)
directory = path.expanduser(directory)
directory = path.abspath(directory)

sys.path.append(directory)

import pwnellij  # noqa: E402, F401  -- intentional load after sys.path setup
