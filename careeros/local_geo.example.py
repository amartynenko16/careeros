"""Your geo criteria for job filtering. Copy this file to local_geo.py
(gitignored, so your real location never gets committed) and fill in your
own commute area.

See CLAUDE.md's "Job search criteria" section for the full picture -- this
file is just the two lists geo_eligible() (in careeros/fetchers/base.py)
matches against.
"""

# Hybrid/onsite roles are only geo-eligible if their location matches one of
# these terms (case-insensitive substring match). Use lowercase city/region
# names for your own commute area.
TARGET_GEO_AREA = ["your-city", "nearby-city-one", "nearby-city-two"]

# Remote roles with one of these terms anywhere in their location string are
# treated as open to you regardless of TARGET_GEO_AREA -- e.g. your country
# name, or broader terms like "global"/"worldwide" if you'd take fully
# unrestricted remote work.
OPEN_REMOTE_TERMS = ["global", "worldwide", "anywhere", "your-country"]
