import logging
from multiprocessing import Event
import re
import time
import threading
from typing import Any, Dict, List, Optional, Tuple, Union

from asitiger.axis import Axis
from asitiger.command import Command, CRISPState
from asitiger.errors import Errors
from asitiger.secure import SecurePosition
from asitiger.serialconnection import SerialConnection
from asitiger.status import AxisStatus, CRISPStatus, Status, statuses_for_rdstat

SAFE_STAGE_LIMITS = {'X': (-65000, 65000), 'Y': (-190000, 190000), 'Z': (-9000, 9800)}

LOGGER = logging.getLogger("asitiger.tigercontroller")


class TigerController:

    DEFAULT_POLL_INTERVAL_S = 0.001

    def __init__(
        self,
        serial_connection: SerialConnection,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        stage_limits: Union[None, Dict[str, Tuple[int, int]]] = SAFE_STAGE_LIMITS,
        stop_event: Union[Event, None] = None,
    ):
        self.connection: SerialConnection = serial_connection
        self.poll_interval_s: float = poll_interval_s
        if not all(ax in stage_limits.keys() for ax in ['X', 'Y', 'Z']):
            raise Errors.MissingParametersError("Missing keys in stage limits. Returning.")
        self._stage_limits: Union[None, Dict[str, Tuple[int, int]]] = stage_limits
        self._lock: threading.RLock = threading.RLock()
        self._stop_event: Union[Event, None] = stop_event

    @classmethod
    def from_serial_port(
        cls, port: str, baud_rate: int = 115200, *tiger_args, **tiger_kwargs
    ) -> "TigerController":
        return cls(SerialConnection(port, baud_rate), *tiger_args, **tiger_kwargs)

    def send_command(self, command: str) -> str:
        with self._lock:
            self.connection.send_command(command)
            response = self.connection.read_response()

        Errors.raise_error_if_present(command, response)

        return response

    def sleep(
            self,
            duration: float,
    ):
        now = time.perf_counter()
        end = now + duration
        while (now < end) and (self._stop_event is None or (not self.stopped())):
            now = time.perf_counter()

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @staticmethod
    def _cast_number(number_str: str):
        try:
            return int(number_str)
        except ValueError:
            return float(number_str)

    @staticmethod
    def _cast_float(value_str: str) -> Union[float, None]:
        pattern = r'^:A\s+'
        try:
            value = float(re.sub(pattern, '', value_str))
        except ValueError:
            value = None
            LOGGER.warning(
                f'String "{value_str}" cannot be converted to float. Returning None instead.'
            )
        return value

    @staticmethod
    def _cast_int(value_str: str) -> Union[int, None]:
        pattern = r'^:A\s+'
        try:
            value = int(re.sub(pattern, '', value_str))
        except ValueError:
            value = None
            LOGGER.warning(
                f'String "{value_str}" cannot be converted to int. Returning None instead.'
            )
        return value

    @staticmethod
    def _dict_from_response(
        serial_response: str, cast_values_to=None
    ) -> Dict[str, Any]:
        tokens = re.split(r"\s+", serial_response.strip())
        key_value_pairs = map(lambda pair: pair.split("="), tokens[1:])
        cast = cast_values_to if cast_values_to is not None else lambda value: value

        return {key: cast(value) for key, value in key_value_pairs}

    # The methods below are higher-level convenience methods that
    # don't necessarily map directly onto supported serial commands

    def filter_wheel(self, position: int, card_address: int = 8):
        self.send_command(Command.format(Command.FW.format(position), card_address=card_address))

    def axes(self, card_address: Optional[int] = None) -> List[Axis.AxisInfo]:
        return Axis.get_axes_from_build(self.build(card_address=card_address))

    def is_busy(self, card_address_crisp: Optional[int] = None) -> bool:
        if card_address_crisp is None:
            return self.status() is Status.BUSY
        else:
            return self.status() is Status.BUSY or self.status_crisp(card_address_crisp) is CRISPStatus.OUT_OF_FOCUS

    def wait_until_idle(self, poll_interval_s: float = None, card_address_crisp: Optional[int] = None):
        poll_interval_s = poll_interval_s if poll_interval_s else self.poll_interval_s

        while self.is_busy(card_address_crisp=card_address_crisp):
            self.sleep(poll_interval_s)

    def enable_axes(self, axes: List[str]):
        self.motor_control({axis: "+" for axis in axes})

    def disable_axes(self, axes: List[str]):
        self.motor_control({axis: "-" for axis in axes})

    def set_plate_lock(
        self, position: Union[SecurePosition, float], card_address: int = None
    ):
        return self.secure(
            {"X": SecurePosition.resolve_value(position)}, card_address=card_address
        )

    def get_stage_limits(self) -> Dict[str, Tuple[int, int]]:
        return {key: val for key, val in self._stage_limits.items()}

    def set_stage_limits(self, stage_limits: Dict[str, Tuple[int, int]]):
        if not all(ax in stage_limits.keys() for ax in ['X', 'Y', 'Z']):
            raise Errors.MissingParametersError(f"Missing keys in stage limits: {stage_limits}. Returning.")
        self._stage_limits = {key: val for key, val in stage_limits.items()}

    def set_stage_limits_relative(self, relative_limits: Dict[str, int]):
        curr_pos = self.where()
        new_stage_limits = self.get_stage_limits()
        for key in relative_limits.keys():
            if relative_limits[key] > 0:
                raise Errors.ParameterOutOfRangeError(
                    f"TigerController.set_stage_limits_relative: Relative limits must be positive. "
                    f"Received {relative_limits}."
                )
            new_stage_limits[key] = (curr_pos[key] - relative_limits[key], curr_pos[key] + relative_limits[key])
        self.set_stage_limits(stage_limits=new_stage_limits)

    # The methods below map directly onto the Tiger serial API methods

    def build(self, card_address: int = None) -> List[str]:
        response = self.send_command(
            Command.format(f"{Command.BUILD} X", card_address=card_address)
        )
        return response.split("\r")

    def halt(self):
        self.send_command(Command.HALT)

    def here(self, coordinates: Dict[str, float]) -> str:
        return self.send_command(Command.format(Command.HERE, coordinates=coordinates))

    def home(self, axes: Union[List[str], None] = None) -> str:
        if axes is None:
            axes = ['X', 'Y', 'Z']
        return self.send_command(f"{Command.HOME} {' '.join(axes)}")

    def led(self, led_brightnesses: Dict[str, int], card_address: int = None):
        self.send_command(
            Command.format(
                Command.LED, coordinates=led_brightnesses, card_address=card_address
            )
        )

    def motor_control(self, axes_states: Dict[str, str]):
        self.send_command(
            Command.format(Command.MOTCTRL, axes_states, flag_overrides=["+", "-"])
        )

    def coordinate_is_out_of_bounds(self, coordinates: Dict[str, float]) -> bool:
        return False if self._stage_limits is None else any((key not in self._stage_limits) or
                                                            (val < self._stage_limits[key][0]) or
                                                            (val > self._stage_limits[key][1])
                                                            for key, val in coordinates.items())

    def move(self, coordinates: Dict[str, float]) -> Union[str, Errors.ParameterOutOfRangeError]:
        """
        See http://asiimaging.com/docs/commands/um
        Position is specified in 1/10 of um, i.e. X=1234 means 123.4 microns
        """
        if self.coordinate_is_out_of_bounds(coordinates):
            LOGGER.warning(f"TigerController.move: Coordinates {coordinates} are outside of admissible range"
                           f"({self._stage_limits}). ")
            raise Errors.ParameterOutOfRangeError()
        return self.send_command(Command.format(Command.MOVE, coordinates=coordinates))

    def move_relative(self, offsets: Dict[str, float]):
        return self.send_command(Command.format(Command.MOVREL, coordinates=offsets))

    def rdstat(self, axes: List[str]) -> List[Union[AxisStatus, Status]]:
        response = self.send_command(f"{Command.RDSTAT} {' '.join(axes)}")
        return statuses_for_rdstat(response)

    def secure(
        self, settings: Dict[str, Union[int, float, str]], card_address: int = None,
    ):
        self.send_command(
            Command.format(Command.SECURE, settings, card_address=card_address)
        )

    def set_home(self, axes: Dict[str, Union[str, int]]) -> str:
        return self.send_command(
            Command.format(Command.SETHOME, coordinates=axes, flag_overrides=["+"])
        )

    def speed(self, axes: Dict[str, Union[str, float]]) -> Dict[str, float]:
        command = Command.format(Command.SPEED, coordinates=axes, flag_overrides=["?"])
        return self._dict_from_response(self.send_command(command))

    def status(self) -> Status:
        return Status(self.send_command(Command.STATUS))

    def status_crisp(self, card_address: int) -> CRISPStatus:
        return CRISPStatus(self.crisp_get_set_state(card_address=card_address, value=None))

    def where(self, axes: Union[List[str], None] = None) -> dict:
        if axes is None:
            axes = ['X', 'Y', 'Z']
        response = self.send_command(f"{Command.WHERE} {' '.join(axes)}")
        coordinates = response.split(" ")[1:]

        return {
            axis: self._cast_number(coord) for axis, coord in zip(axes, coordinates)
        }

    def who(self) -> List[str]:
        return self.send_command(Command.WHO).split("\r")

    def zero(self) -> str:
        return self.send_command(f"{Command.ZERO}")

    # The methods below are CRISP autofocus functions (set_xxx first, get_xxx below)

    def crisp_get_set_cal_range(self, card_address: int, value: Union[float, None]) -> str:
        return self.send_command(Command.format_crisp(Command.CRISP_CAL_RANGE, card_address, value))

    def crisp_get_set_led_intensity(self, card_address: int, value: Union[int, None]) -> Union[str, int]:
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_LED, card_address, value))
        else:
            pattern = r'^[^0-9]*([0-9]+).*'
            return self._cast_int(
                re.sub(pattern, r'\1', self.send_command(Command.format_crisp(Command.CRISP_LED, card_address, value)))
            )

    def crisp_get_set_lock_range(self, card_address: int, value: Union[float, None]) -> Union[str, float]:
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_LOCK_RANGE, card_address, value))
        else:
            pattern = r':A Z=([0-9]+\.[0-9]+)'
            return self._cast_float(re.sub(
                pattern,
                r'\1',
                self.send_command(Command.format_crisp(Command.CRISP_LOCK_RANGE, card_address, value))
            ))

    def crisp_get_set_loop_gain(self, card_address: int, value: Union[int, None]) -> Union[str, int]:
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_LOOP_GAIN, card_address, value))
        else:  # Need some hacking here as ASI returns a float instead of an integer
            pattern = r':A T=([0-9]+\.[0-9]+)'
            return int(float(re.sub(
                pattern,
                r'\1',
                self.send_command(Command.format_crisp(Command.CRISP_LOOP_GAIN, card_address, value))))
            )

    def crisp_get_set_num_avg(self, card_address: int, value: Union[int, None]) -> Union[str, int]:
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_NUM_AVG, card_address, value))
        else:  # Need some hacking here as ASI returns a float instead of an integer
            pattern = r':A F=([0-9]+\.[0-9]+)'
            return int(float(re.sub(
                pattern,
                r'\1',
                self.send_command(Command.format_crisp(Command.CRISP_NUM_AVG, card_address, value))))
            )

    def crisp_get_set_objective_na(self, card_address: int, value: Union[float, None]) -> Union[str, float]:
        pattern = r'^[^0-9]*([0-9]+\.[0-9]+).*'
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_NA, card_address, value))
        else:
            return self._cast_float(re.sub(
                pattern,
                r'\1',
                self.send_command(Command.format_crisp(Command.CRISP_NA, card_address, value))
            ))

    def crisp_get_set_state(self, card_address: int, value: Union[CRISPState, None]) -> str:
        pattern = r'^:A\s+'
        if value == CRISPState.UNLOCK:
            return re.sub(
                pattern,
                '',
                self.send_command(f"{card_address}{Command.CRISP_UNLOCK}")
            )
        else:
            this_command = Command.CRISP_SET_STATE if value else Command.CRISP_GET_STATE
            return re.sub(
                pattern,
                '',
                self.send_command(Command.format_crisp(this_command, card_address, value))
            )

    def crisp_get_set_update_rate(self, card_address: int, value: Union[int, None]) -> Union[str, int]:
        if value is not None:
            return self.send_command(Command.format_crisp(Command.CRISP_UPDATE_RATE, card_address, value))
        else:
            pattern = r'^[^0-9]*([0-9]+).*'
            return self._cast_int(re.sub(
                pattern,
                r'\1',
                self.send_command(Command.format_crisp(Command.CRISP_UPDATE_RATE, card_address, value)))
            )

    def crisp_get_agc(self, card_address: int) -> str:
        return self.send_command(Command.format_crisp(Command.CRISP_AGC, card_address, None))

    def crisp_get_snr(self, card_address: int) -> float:
        return self._cast_float(
            self.send_command(Command.format_crisp(Command.CRISP_SNR, card_address, None))
        )

    def crisp_get_sum(self, card_address: int) -> str:
        return self.send_command(Command.format_crisp(Command.CRISP_SUM, card_address, None))

    def crisp_get_err(self, card_address: int) -> int:
        return self._cast_int(
            self.send_command(Command.format_crisp(Command.CRISP_ERROR_NUM, card_address, None))
        )

    def crisp_reset_offset(self, card_address: int) -> str:
        return self.crisp_get_set_state(card_address=card_address, value=CRISPState.SET_OFFSET)
