"""Regression fence for bugs fixed during the power_strategy rewrite.

These tests encode historical bugs as explicit scenarios so they cannot silently
return. Add a test here whenever a power_strategy bug is fixed.

Current fixtures:
- MANUAL mode must bypass hysteresis cooldown (re-arm loop bug)
- WAKE_PENDING must fall back to IDLE after WAKE_TIMEOUT (sticky WAKEUP bug)
- Stopping a charging device must route through power_discharge, not power_charge(0)
  (SF 2400 oscillation quirk)
"""
