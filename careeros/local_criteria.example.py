"""Personal scoring thresholds for scoring.py. Copy this file to
local_criteria.py (gitignored, so your real numbers never get committed)
and fill in your own comp floor.
"""

# Hard comp floor used by the Fit Score's comp-attractiveness component.
# Currency-blind by design (scoring.py doesn't attempt currency conversion) --
# set this to whatever currency most of your target roles post in.
COMP_FLOOR = 120000
COMP_FLOOR_CURRENCY = "CAD"
