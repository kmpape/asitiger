import logging
from typing import Dict, List, Union

LOGGER = logging.getLogger("asitiger.command")


class CRISPState:
    FOCUS_CURVE = 97  # Generate focus curve
    DITHER = 102      # Calibration Step 3
    IDLE = 79         # Calibration Step 1, LED is tuned off going from Ready to Idle
    LOCK = 83         # Focus Lock
    LOG_CAL = 72      # Calibration Step 2
    READY = 85        # LED on - @ button locks
    SET_GAIN = 67     # Calibration Step 4
    SET_OFFSET = 111  # Reset focus offset
    OUT_OF_FOCUS = 83
    UNLOCK = -1       # Unlock Focus
    str2state = {'I': IDLE, 'R': READY, 'G': LOG_CAL, 'F': LOCK, 'K': OUT_OF_FOCUS}


class Command:
    BUILD = "BU"
    CRISP_AGC = "AL X"
    CRISP_CAL_RANGE = "LR F"
    CRISP_ERROR_NUM = "LK Y"
    CRISP_GET_STATE = "LK X"
    CRISP_LED = "UL X"
    CRISP_LOCK_RANGE = "LR Z"
    CRISP_LOOP_GAIN = "LR T"
    CRISP_NA = "LR Y"
    CRISP_NUM_AVG = "RT F"
    CRISP_SET_STATE = "LK F"
    CRISP_SNR = "EXTRA Y"
    CRISP_SUM = "LK T"
    CRISP_UNLOCK = "UL"
    CRISP_UPDATE_RATE = "UL Y"
    FW = "MP {}"
    HALT = "\\"
    HERE = "H"
    HOME = "!"
    INFO = "I"
    LED = "LED"
    MOTCTRL = "MC"
    MOVE = "M"
    MOVREL = "R"
    RDSTAT = "RS"
    SECURE = "SECURE"
    SETHOME = "HM"
    SPEED = "S"
    STATUS = "/"
    WHERE = "W"
    WHO = "WHO"
    ZERO = "Z"

    _NUMERAL_MAX_LENGTH = 16

    @classmethod
    def format(
        cls,
        command: str,
        coordinates: Dict[str, Union[float, str]] = None,
        flag_overrides: List[str] = None,
        card_address: int = None,
    ):
        if coordinates:
            formatted_coords = cls.format_coordinates(
                coordinates, flag_overrides=flag_overrides
            )
            command = f"{command} {formatted_coords}"

        if card_address:
            command = f"{card_address}{command}"

        return command

    @classmethod
    def format_coordinates(
        cls, coordinates: Dict[str, Union[float, str]], flag_overrides: List[str] = None
    ):
        return " ".join(
            map(
                lambda coord: cls.format_coordinate(
                    coord[0], coord[1], flag_overrides=flag_overrides
                ),
                coordinates.items(),
            )
        )

    @classmethod
    def format_coordinate(
        cls, axis: str, value: Union[str, float], flag_overrides: List[str] = None
    ) -> str:
        value_is_flag = flag_overrides and value in flag_overrides

        if value_is_flag:
            return f"{axis}{value}"

        if len(str(value)) > cls._NUMERAL_MAX_LENGTH:
            truncated_value = str(value)[: cls._NUMERAL_MAX_LENGTH]
            LOGGER.warning(
                f'Numeral "{value}" is too long for the instrument, it will be truncated to: "{truncated_value}"'
            )
            value = truncated_value

        return f"{axis}={value}"

    @classmethod
    def format_crisp(
        cls,
        command: str,
        card_address: int,
        value: Union[None, int, float, str] = None,
    ):
        if isinstance(value, float) and len(str(value)) > cls._NUMERAL_MAX_LENGTH:
            truncated_value = str(value)[: cls._NUMERAL_MAX_LENGTH]
            LOGGER.warning(
                f'Numeral "{value}" is too long for the instrument, it will be truncated to: "{truncated_value}"'
            )
            value = truncated_value

        if value is not None:
            command = f"{card_address}{command}={value}"
        else:
            command = f"{card_address}{command}?"

        return command
