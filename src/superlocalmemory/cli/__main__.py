# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Allow ``python -m superlocalmemory.cli`` invocation.

Required for subprocess-driven test harnesses that invoke the CLI as:
    python -m superlocalmemory.cli <command> [args...]

The production ``slm`` console script calls the same entry point:
    slm = "superlocalmemory.cli.main:main"
"""

from superlocalmemory.cli.main import main

if __name__ == "__main__":
    main()
