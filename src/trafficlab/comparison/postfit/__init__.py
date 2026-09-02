"""Final-only traffic-structure diagnostics.

These functions deliberately have no dependency on fitness evaluation or
artifact publication.  The final comparison boundary owns that integration.
"""

from trafficlab.comparison.postfit.c2st import classical_c2st_diagnostic
from trafficlab.comparison.postfit.dispersion import fano_allan_diagnostic
from trafficlab.comparison.postfit.transitions import transition_matrix_diagnostic

__all__ = ["classical_c2st_diagnostic", "fano_allan_diagnostic", "transition_matrix_diagnostic"]
