"""
IntelliAide API Package

A comprehensive must-gather analysis system for OpenShift diagnostics.
"""

__version__ = "3.0.0"
__author__ = "IntelliAide Team"

# Import core functionality for easy access
from .core import OrchestratorAgent, DataAnalyzer

__all__ = ['OrchestratorAgent', 'DataAnalyzer']