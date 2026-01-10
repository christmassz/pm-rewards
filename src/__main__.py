"""
Entry point for running src module with subcommands.

Supports:
- python -m src.selector [args]
- python -m src.maker [args] (when implemented)
- python -m src.main [args] (when implemented)
"""

import sys

if __name__ == '__main__':
    # Get the submodule name from argv
    if len(sys.argv) < 2:
        print("Usage: python -m src <submodule> [args]")
        print("Available submodules: selector")
        sys.exit(1)

    submodule = sys.argv[1]
    # Remove the submodule name from args
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if submodule == 'selector':
        from .selector import main
        main()
    else:
        print(f"Unknown submodule: {submodule}")
        print("Available submodules: selector")
        sys.exit(1)