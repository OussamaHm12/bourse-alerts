"""The analyst team: ten independent analysts + Risk Manager + CIO.

Every analyst exposes ``analyze(ctx) -> AnalystReport`` and NEVER recommends.
Only :mod:`cio` produces a recommendation.
"""
