"""Test script for the Liverpool Philharmonic Hall extractor implementation."""
import os
import sys

# Ensure the root project directory is on the system path for seamless module resolution
sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from scrapers.philharmonic_hall.run_extractor import (  # noqa: E402
    PhilharmonicHallExtractor,
)
from utils.logger import setup_logger  # noqa: E402

logger = setup_logger("test_philharmonic_hall", log_to_file=False)


def test_philharmonic_hall_pipeline():
    """Executes a framework validation run against the Philharmonic Hall extractor."""
    logger.info(" Starting Philharmonic Hall Pipeline Test Run")

    # Initialize using the framework configuration parameters
    extractor = PhilharmonicHallExtractor(
        local_test=True,  # Restricts processing to a smaller subset of shows
        show_count=None,  # Limits processing to 2 shows for rapid end-to-end iteration
        save_csv_locally=True,  # Saves a verification file directly to the data/ folder
        csv_incremental_mode=False,
    )

    # Run the core pipeline lifecycle (Extract -> Save Raw -> Parse -> Validate Schema -> Save CSV)
    result = extractor.run()
    logger.info(f" Pipeline Test Completed. Result Summary: {result}")


if __name__ == "__main__":
    test_philharmonic_hall_pipeline()
