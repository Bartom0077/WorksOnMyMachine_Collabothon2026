"""``python -m kpi_engine --as-of-date YYYY-MM-DD`` — see runner.main."""
import sys

from .runner import main

sys.exit(main())
