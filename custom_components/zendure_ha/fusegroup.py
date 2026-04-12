"""Fusegroup for Zendure devices."""

from __future__ import annotations

import logging

from .device import ZendureDevice

_LOGGER = logging.getLogger(__name__)


class FuseGroup:
    """Zendure Fuse Group."""

    def __init__(self, name: str, maxpower: int, minpower: int, devices: list[ZendureDevice] | None = None) -> None:
        """Initialize the fuse group."""
        self.name: str = name
        self.maxpower = maxpower
        self.minpower = minpower
        self.devices: list[ZendureDevice] = devices if devices is not None else []
        for d in self.devices:
            d.fuseGrp = self

    def update_charge_limits(self) -> None:
        """Return the limit discharge power for a device."""
        if len(self.devices) == 1:
            d = self.devices[0]
            d.pwr_max = max(self.minpower, d.charge_limit)
        else:
            limit = 0
            weight = 0
            for fd in self.devices:
                if fd.acPort.is_charging:
                    limit += fd.charge_limit
                    weight += (100 - fd.electricLevel.asInt) * fd.charge_limit

            avail = max(self.minpower, limit)

            for fd in self.devices:
                if fd.acPort.is_charging:
                    if weight < 0: # Sicherheitscheck aus dem Originalcode
                        fd.pwr_max = fd.charge_start
                    else:
                        fd.pwr_max = int(avail * ((100 - fd.electricLevel.asInt) * fd.charge_limit) / weight)

                    limit -= fd.charge_limit
                    if limit > avail - fd.pwr_max:
                        fd.pwr_max = max(avail - limit, avail)
                    fd.pwr_max = max(fd.pwr_max, fd.charge_limit)
                    avail -= fd.pwr_max

    def update_discharge_limits(self) -> None:
        """Return the limit discharge power for a device."""
        if len(self.devices) == 1:
            d = self.devices[0]
            d.pwr_max = min(self.maxpower, d.discharge_limit)
        else:
            limit = 0
            weight = 0
            for fd in self.devices:
                if fd.acPort.is_discharging:
                    limit += fd.discharge_limit
                    weight += fd.electricLevel.asInt * fd.discharge_limit

            avail = min(self.maxpower, limit)

            for fd in self.devices:
                if fd.acPort.is_discharging:
                    if weight > 0:
                        fd.pwr_max = int(avail * (fd.electricLevel.asInt * fd.discharge_limit) / weight)
                    else:
                        fd.pwr_max = fd.discharge_start

                    limit -= fd.discharge_limit
                    if limit < avail - fd.pwr_max:
                        fd.pwr_max = min(avail - limit, avail)
                    fd.pwr_max = min(fd.pwr_max, fd.discharge_limit)
                    avail -= fd.pwr_max