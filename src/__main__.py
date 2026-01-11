"""
Entry point for running src module with subcommands.

Supports:
- python -m src.selector [args]
- python -m src.maker [args]
- python -m src.main [args]
"""

import sys

if __name__ == '__main__':
    # Special case: if no args or first arg starts with '-', assume main orchestrator
    if len(sys.argv) < 2 or sys.argv[1].startswith('-'):
        from .main import main
        main()
    else:
        # Get the submodule name from argv
        submodule = sys.argv[1]
        # Remove the submodule name from args
        sys.argv = [sys.argv[0]] + sys.argv[2:]

        if submodule == 'selector':
            from .selector import main
            main()
        elif submodule == 'maker':
            from .maker import main
            main()
        elif submodule == 'main':
            from .main import main
            main()
        else:
            print(f"Unknown submodule: {submodule}")
            print("Available submodules: selector, maker, main")
            sys.exit(1)