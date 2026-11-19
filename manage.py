#!/usr/bin/env python
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main(["manage", *sys.argv[1:]]))
