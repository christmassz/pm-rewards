#!/usr/bin/env python3
"""
PM-Rewards Loop Runner

Runs the PM-rewards system in a continuous loop with configurable intervals.
Handles graceful shutdown and provides status logging.

Usage:
    python start.py

Configuration:
    loop_interval_sec in config.yaml controls the interval between runs
"""

import time
import signal
import sys
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from config import load_config_or_default

# Global flag for graceful shutdown
shutdown_requested = False

def setup_logging():
    """Configure logging for the loop runner."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('logs/start_loop.log', mode='a')
        ]
    )

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    logging.info(f"Received {signal_name} signal, initiating graceful shutdown...")
    shutdown_requested = True

def run_orchestrator(config):
    """
    Run the main orchestrator for one cycle.

    Returns:
        bool: True if successful, False if failed
    """
    try:
        # Determine run duration - use a reasonable default for loop mode
        run_duration = min(config.get('loop_interval_sec', 600) - 30, 300)  # Leave 30s buffer, max 5min

        logging.info(f"Starting orchestrator cycle (duration: {run_duration}s)")

        # Run the main orchestrator in paper mode
        result = subprocess.run([
            sys.executable, '-m', 'src.main',
            '--paper',
            '--seconds', str(run_duration)
        ],
        capture_output=True,
        text=True,
        timeout=run_duration + 60  # Add timeout buffer
        )

        if result.returncode == 0:
            logging.info("Orchestrator cycle completed successfully")
            return True
        else:
            logging.error(f"Orchestrator failed with return code {result.returncode}")
            if result.stderr:
                logging.error(f"Error output: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logging.error("Orchestrator cycle timed out")
        return False
    except Exception as e:
        logging.error(f"Error running orchestrator: {e}")
        return False

def main_loop():
    """
    Main loop that runs the orchestrator at configured intervals.
    """
    global shutdown_requested

    # Load configuration
    try:
        config = load_config_or_default('config.yaml')
        loop_interval = config.loop_interval_sec  # Use dot notation for dataclass
        logging.info(f"Loaded configuration: loop interval = {loop_interval}s ({loop_interval/60:.1f} minutes)")
    except Exception as e:
        logging.error(f"Failed to load configuration: {e}")
        return 1

    # Statistics
    cycle_count = 0
    success_count = 0
    start_time = datetime.now()

    logging.info("=== PM-Rewards Loop Runner Started ===")
    logging.info(f"Loop interval: {loop_interval}s ({loop_interval/60:.1f} minutes)")
    logging.info("Press Ctrl+C to stop gracefully")

    try:
        while not shutdown_requested:
            cycle_start = datetime.now()
            cycle_count += 1

            logging.info(f"--- Cycle {cycle_count} started at {cycle_start.strftime('%H:%M:%S')} ---")

            # Run selector first to update target markets
            logging.info("Running market selector...")
            try:
                selector_result = subprocess.run([
                    sys.executable, '-m', 'src.selector',
                    '--select-top', '--write'
                ], capture_output=True, text=True, timeout=120)

                if selector_result.returncode == 0:
                    logging.info("Market selector completed successfully")
                else:
                    logging.warning("Market selector failed, continuing with existing markets")
            except Exception as e:
                logging.warning(f"Market selector error: {e}, continuing with existing markets")

            # Run orchestrator
            success = run_orchestrator(config)
            if success:
                success_count += 1

            cycle_end = datetime.now()
            cycle_duration = (cycle_end - cycle_start).total_seconds()

            logging.info(f"--- Cycle {cycle_count} completed in {cycle_duration:.1f}s (success: {success}) ---")
            logging.info(f"Statistics: {success_count}/{cycle_count} successful cycles")

            # Wait for next cycle (unless shutdown requested)
            if not shutdown_requested:
                logging.info(f"Waiting {loop_interval}s until next cycle...")

                # Sleep in small intervals to check for shutdown
                sleep_remaining = loop_interval
                while sleep_remaining > 0 and not shutdown_requested:
                    sleep_time = min(sleep_remaining, 5)  # Check every 5 seconds
                    time.sleep(sleep_time)
                    sleep_remaining -= sleep_time

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received")
        shutdown_requested = True

    # Final statistics
    total_runtime = (datetime.now() - start_time).total_seconds()
    logging.info("=== PM-Rewards Loop Runner Stopped ===")
    logging.info(f"Total runtime: {total_runtime:.1f}s ({total_runtime/60:.1f} minutes)")
    logging.info(f"Completed cycles: {cycle_count}")
    logging.info(f"Successful cycles: {success_count}")
    if cycle_count > 0:
        logging.info(f"Success rate: {success_count/cycle_count*100:.1f}%")

    return 0

def main():
    """
    Main entry point for the loop runner.
    """
    # Ensure logs directory exists
    Path('logs').mkdir(exist_ok=True)

    # Setup logging
    setup_logging()

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the main loop
    try:
        exit_code = main_loop()
        sys.exit(exit_code)
    except Exception as e:
        logging.error(f"Fatal error in main loop: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()