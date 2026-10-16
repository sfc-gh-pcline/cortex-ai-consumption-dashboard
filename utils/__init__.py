"""Shared helpers for the Cortex AI Consumption Dashboard.

This file makes ``utils`` a regular package. Without it, Python treats ``utils``
as a namespace package and merges every ``utils/`` directory on the module search
path, so a stale copy left in a hosted container can keep supplying old modules
next to new ones. A regular package resolves to exactly one directory.
"""
