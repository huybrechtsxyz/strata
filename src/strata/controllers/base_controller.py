"""Base controller class for Strata."""

from typing import List

from strata.logger import get_logger


class BaseController:
    """Base controller providing shared error/message accumulation and logging."""

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__module__)
        self._errors: List[str] = []
        self._messages: List[str] = []

    # Error / message accumulation helpers

    def has_errors(self) -> bool:
        return bool(self._errors)

    def get_errors(self) -> List[str]:
        return list(self._errors)

    def clear_errors(self) -> None:
        self._errors.clear()

    def get_messages(self) -> List[str]:
        return list(self._messages)

    def has_messages(self) -> bool:
        return bool(self._messages)

    def clear_messages(self) -> None:
        self._messages.clear()

    def _add_error(self, message: str) -> None:
        """Log *message* at ERROR level and append it to the error list."""
        self.logger.error(message)
        self._errors.append(message)

    def _add_message(self, message: str) -> None:
        """Log *message* at INFO level and append it to the message list."""
        self.logger.info(message)
        self._messages.append(message)
