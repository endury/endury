"""Shaping the coach's reply after the model has spoken.

``postprocess`` cleans the reply text (echo stripping, the one-question
rule, leaked tool-call blocks) and ``finalizer`` applies the turn's profile
side effects at the end of a turn.
"""
