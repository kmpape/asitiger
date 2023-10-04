import logging
import re
import time
from typing import Any, Dict, List, Tuple, Union

from asitiger.axis import Axis
from asitiger.command import Command, CRISPState
from asitiger.errors import Errors
from asitiger.secure import SecurePosition
from asitiger.serialconnection import SerialConnection
from asitiger.status import AxisStatus, Status, statuses_for_rdstat

SAFE_STAGE_LIMITS = {'X': (-450000, 9800), 'Y': (-150000, 427000), 'Z': (-77000, 9800)}

LOGGER = logging.getLogger("asitiger.tigercontroller")

class TigerController:

    DEFAULT_POLL_INTERVAL_S = 0.01

    def __init__(
        self,
        serial_connection: SerialConnection,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ):
        self.connection = serial_connection
        self.poll_interval_s = poll_interval_s

    @classmethod
    def from_serial_port(
        cls, port: str, baud_rate: int = 115200, *tiger_args, **tiger_kwargs
    ) -> "TigerController":
        return cls(SerialConnection(port, baud_rate), *tiger_args, **tiger_kwargs)

    def send_command(self, command: str) -> str:
        self.connection.send_command(command)
        response = self.connection.read_response()

        Errors.raise_error_if_present(command, response)

        return response

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

    def axes(self, card_address: int = None) -> List[Axis.AxisInfo]:
        return Axis.get_axes_from_build(self.build(card_address=card_address))

    def is_busy(self) -> bool:
        return self.status() is Status.BUSY

    def wait_until_idle(self, poll_interval_s: float = None):
        poll_interval_s = poll_interval_s if poll_interval_s else self.poll_interval_s

        while self.is_busy():
            time.sleep(poll_interval_s)

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

    def get_stage_limits_mum(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        lower_lim = {key: self._cast_number(val)*1000.0
                     for key, val in self._dict_from_response(self.send_command('SL X? Y? Z?')).items()}
        upper_lim = {key: self._cast_number(val)*1000.0
                     for key, val in self._dict_from_response(self.send_command('SU X? Y? Z?')).items()}
        return lower_lim, upper_lim

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

    def home(self, axes: List[str]) -> str:
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

    def move(self, coordinates: Dict[str, float]) -> Union[str, Errors.ParameterOutOfRangeError]:
        """
        See http://asiimaging.com/docs/commands/um
        Position is specified in 1/10 of um, i.e. X=1234 means 123.4 microns
        """
        if any((key not in SAFE_STAGE_LIMITS) or (val < SAFE_STAGE_LIMITS[key][0]) or (val > SAFE_STAGE_LIMITS[key][1])
               for key, val in coordinates.items()):
            return Errors.ParameterOutOfRangeError()
        else:
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

    def where(self, axes: List[str]) -> dict:
        response = self.send_command(f"{Command.WHERE} {' '.join(axes)}")
        coordinates = response.split(" ")[1:]

        return {
            axis: self._cast_number(coord) for axis, coord in zip(axes, coordinates)
        }

    def who(self) -> List[str]:
        return self.send_command(Command.WHO).split("\r")

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
