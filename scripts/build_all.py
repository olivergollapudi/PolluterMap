"""
Rebuild everything, in the right order:
  1. ECHO facilities (must come first: the others link to it)
  2. FRS ID crosswalk (NEI and water link through it)
  3. TRI toxic releases
  4. GHG greenhouse gas emissions
  5. NEI air emissions
  6. Water discharges and permit violations
  7. RSEI risk scores (links through TRI)
  8. Toxic releases scored in disease cases (EPA TRACI)
  9. Impact factors (health, climate, removal cost, everyday comparisons, inflation)
 10. Per-facility impact estimates (dollars, deaths, comparisons)
 11. Classic map data (site/data/facilities.geojson)
 12. Website data (site/site-data.js, zips.js, towns.js, share pages in site/p/)

    python3 scripts/build_all.py           # reuse downloaded files
    python3 scripts/build_all.py --force   # re-download everything from EPA/Census first
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["build_echo_indiana.py", "build_frs_indiana.py", "build_tri_indiana.py", "build_ghg_indiana.py",
         "build_nei_indiana.py", "build_water_indiana.py", "build_rsei_indiana.py", "build_toxic_impacts_indiana.py", "build_impact_factors.py",
         "build_impacts_indiana.py", "export_map_data.py", "export_site_data.py"]

for script in STEPS:
    print(f"\n=== {script}")
    args = [sys.executable, str(HERE / script)]
    if "--force" in sys.argv[1:] and script not in ("build_impacts_indiana.py", "export_map_data.py", "export_site_data.py"):
        args.append("--force")
    if subprocess.run(args).returncode != 0:
        sys.exit(f"Stopped: {script} failed.")
print("\nAll done.")
