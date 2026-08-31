from __future__ import annotations

import matplotlib

from trafficlab_dashboard.plotting.canvas import DashboardCanvas
from trafficlab_dashboard.plotting.export import export_figure
from trafficlab_dashboard.plotting.interaction import AxisView, pan_limits, zoom_limits

matplotlib.rcParams["svg.fonttype"] = "none"

__all__ = [
    "AxisView",
    "DashboardCanvas",
    "export_figure",
    "pan_limits",
    "zoom_limits",
]
