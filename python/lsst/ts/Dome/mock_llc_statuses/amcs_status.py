# This file is part of ts_Dome.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__all__ = ["AmcsLimits"]

import logging
import math

import numpy as np

from lsst.ts import salobj
from .base_mock_status import BaseMockStatus
from ..llc_configuration_limits.amcs_limits import AmcsLimits
from ..on_off import OnOff
from lsst.ts.idl.enums.Dome import MotionState

_NUM_MOTORS = 5


class AmcsStatus(BaseMockStatus):
    """Represents the status of the Azimuth Motion Control System in simulation
    mode.
    """

    def __init__(self):
        super().__init__()
        self.log = logging.getLogger("MockAzcsStatus")
        self.amcs_limits = AmcsLimits()
        # default values which may be overriden by calling moveAz, crawlAz or
        # config
        self.jmax = self.amcs_limits.jmax
        self.amax = self.amcs_limits.amax
        self.vmax = self.amcs_limits.vmax
        # the velocity during a move or a crawl
        self.motion_velocity = 0
        # the velocity during a crawl that follows a move
        self.crawl_velocity = 0
        # variables holding the status of the mock AZ motion. The error codes
        # will be specified in a future Dome Software meeting.
        self.status = MotionState.STOPPED
        self.error = ["No Error"]
        self.fans_enabled = OnOff.OFF
        self.seal_inflated = OnOff.OFF
        self.position_orig = 0.0
        self.position_actual = 0
        self.position_commanded = 0
        self.velocity_actual = 0
        self.velocity_commanded = 0
        self.drive_torque_actual = np.zeros(_NUM_MOTORS, dtype=float)
        self.drive_torque_commanded = np.zeros(_NUM_MOTORS, dtype=float)
        self.drive_current_actual = np.zeros(_NUM_MOTORS, dtype=float)
        self.drive_temperature = np.full(_NUM_MOTORS, 20.0, dtype=float)
        self.encoder_head_raw = np.zeros(_NUM_MOTORS, dtype=float)
        self.encoder_head_calibrated = np.zeros(_NUM_MOTORS, dtype=float)
        self.resolver_raw = np.zeros(_NUM_MOTORS, dtype=float)
        self.resolver_calibrated = np.zeros(_NUM_MOTORS, dtype=float)

    async def determine_status(self, current_tai):
        """Determine the status of the Lower Level Component and store it in
        the llc_status `dict`.
        """
        time_diff = float(current_tai - self.command_time_tai)
        self.log.debug(
            f"current_tai = {current_tai}, self.command_time_tai = {self.command_time_tai}, "
            f"time_diff = {time_diff}"
        )
        if self.status != MotionState.STOPPED:
            azimuth_step = self.motion_velocity * time_diff
            self.position_actual = self.position_orig + azimuth_step

            # perform boundary checks for the current motion or crawl
            if (
                # Check velocity only if not equal to zero to not have to add
                # a case for STOPPED and PARKED.
                self.motion_velocity > 0
                and self.position_actual >= self.position_commanded
            ) or (
                # Check velocity only if not equal to zero to not have to add
                # a case for STOPPED and PARKED.
                self.motion_velocity < 0
                and self.position_actual <= self.position_commanded
            ):
                self.position_actual = self.position_commanded
                if self.status == MotionState.MOVING:
                    if self.crawl_velocity > 0:
                        # make sure that the dome never stops moving because it
                        # is crawling
                        self.position_commanded = math.inf
                        self.motion_velocity = self.crawl_velocity
                        self.status = MotionState.CRAWLING
                    elif self.crawl_velocity < 0:
                        # make sure that the dome never stops moving because it
                        # is crawling
                        self.position_commanded = -math.inf
                        self.motion_velocity = self.crawl_velocity
                        self.status = MotionState.CRAWLING
                    else:
                        # make sure that the dome has stopped moving because
                        # the requested crawl velocity is 0
                        self.position_commanded = self.position_actual
                        self.motion_velocity = self.crawl_velocity
                        self.status = MotionState.STOPPED
                elif self.status == MotionState.PARKING:
                    self.position_commanded = self.position_actual
                    self.motion_velocity = 0
                    self.status = MotionState.PARKED
                elif self.status == MotionState.CRAWLING:
                    # this situation should never happen and probably never
                    # will because the commanded position should have been set
                    # to +/- math.inf
                    raise ValueError(
                        f"Went beyond the limit of {self.position_commanded} while in status "
                        f"{self.status.value}. This should not happen."
                    )
                else:
                    raise ValueError(f"Unknown state {self.status.value}")
        self.llc_status = {
            "status": {
                "error": self.error,
                "status": self.status.value,
                "fans": self.fans_enabled.name,
                "inflate": self.seal_inflated.name,
            },
            "positionActual": self.position_actual,
            "positionCommanded": self.position_commanded,
            "velocityActual": self.velocity_actual,
            "velocityCommanded": self.velocity_commanded,
            "driveTorqueActual": self.drive_torque_actual.tolist(),
            "driveTorqueCommanded": self.drive_torque_commanded.tolist(),
            "driveCurrentActual": self.drive_current_actual.tolist(),
            "driveTemperature": self.drive_temperature.tolist(),
            "encoderHeadRaw": self.encoder_head_raw.tolist(),
            "encoderHeadCalibrated": self.encoder_head_calibrated.tolist(),
            "resolverRaw": self.resolver_raw.tolist(),
            "resolverCalibrated": self.resolver_calibrated.tolist(),
            # DM-26653: The name of this key is still under discussion and
            # could be modified to "timestampUTC"
            "timestamp": current_tai,
        }

        self.log.debug(f"amcs_state = {self.llc_status}")

    async def moveAz(self, position, velocity):
        """Move the dome at maximum velocity to the specified azimuth. Azimuth
        is measured from 0 at north via 90 at east and 180 at south to 270 west
        and 360 = 0. The value of azimuth is not checked for the range between
        0 and 360.

        Parameters
        ----------
        position: `float`
            The azimuth (deg) to move to.
        velocity: `float`
            The velocity (deg/s) at which to crawl once the commanded azimuth
            has been reached at maximum velocity. The velocity is not checked
            against the velocity limits for the dome.
        """
        self.log.debug(f"moveAz with position={position} and velocity={velocity}")
        self.status = MotionState.MOVING
        self.position_orig = self.position_actual
        self.command_time_tai = salobj.current_tai()
        self.position_commanded = position
        self.crawl_velocity = velocity
        self.motion_velocity = self.vmax
        if self.position_commanded < self.position_actual:
            self.motion_velocity = -self.vmax

    async def crawlAz(self, velocity):
        """Crawl the dome in the given direction at the given velocity.

        Parameters
        ----------
        velocity: `float`
            The velocity (deg/s) at which to crawl. The velocity is not checked
            against the velocity limits for the dome.
        """
        self.position_orig = self.position_actual
        self.command_time_tai = salobj.current_tai()
        self.motion_velocity = velocity
        self.status = MotionState.CRAWLING
        if self.motion_velocity >= 0:
            # make sure that the dome never stops moving
            self.position_commanded = math.inf
        else:
            # make sure that the dome never stops moving
            self.position_commanded = -math.inf

    async def stopAz(self):
        """Stop all motion of the dome."""
        self.command_time_tai = salobj.current_tai()
        self.status = MotionState.STOPPED

    async def park(self):
        """Park the dome, meaning that it will be moved to azimuth 0."""
        self.status = MotionState.PARKING
        self.position_orig = self.position_actual
        self.command_time_tai = salobj.current_tai()
        self.position_commanded = 0.0
        self.motion_velocity = -self.vmax

    async def inflate(self, action):
        """Inflate or deflate the inflatable seal.

        This is a placeholder for now until it becomes clear what this command
        is supposed to do.

        Parameters
        ----------
        action: `str`
            The value should be ON or OFF but the value doesn't get validated
            here.
        """
        self.command_time_tai = salobj.current_tai()
        self.seal_inflated = OnOff[action]

    async def fans(self, action):
        """Enable or disable the fans in the dome.

        This is a placeholder for now until it becomes clear what this command
        is supposed to do.

        Parameters
        ----------
        action: `str`
            The value should be ON or OFF but the value doesn't get validated
            here.
        """
        self.command_time_tai = salobj.current_tai()
        self.fans_enabled = OnOff[action]
