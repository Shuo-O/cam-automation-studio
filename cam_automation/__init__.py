"""Reusable offline CAM workflow and Flow Studio core."""

from .flow_api import create_flow_api, register_flow_routes
from .integrations import ReviewGatedFlowService
from .learning import learn_workflow
from .parser import parse_log

FlowService = ReviewGatedFlowService

__all__ = [
    "FlowService",
    "ReviewGatedFlowService",
    "create_flow_api",
    "learn_workflow",
    "parse_log",
    "register_flow_routes",
]
__version__ = "0.7.0"
