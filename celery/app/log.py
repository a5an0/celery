# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging
import sys

from .. import signals
from ..utils import isatty
from ..utils.compat import LoggerAdapter, WatchedFileHandler
from ..utils.log import (ColorFormatter, ensure_process_aware_logger,
                         LoggingProxy, get_multiprocessing_logger,
                         reset_multiprocessing_logger)
from ..utils.term import colored

is_py3k = sys.version_info >= (3, 0)


class Logging(object):
    #: The logging subsystem is only configured once per process.
    #: setup_logging_subsystem sets this flag, and subsequent calls
    #: will do nothing.
    _setup = False

    def __init__(self, app):
        self.app = app
        self.format = self.app.conf.CELERYD_LOG_FORMAT
        self.task_format = self.app.conf.CELERYD_TASK_LOG_FORMAT
        self.colorize = self.app.conf.CELERYD_LOG_COLOR

    def supports_color(self, logfile=None):
        if self.app.IS_WINDOWS:
            # Windows does not support ANSI color codes.
            return False
        if self.colorize is None:
            # Only use color if there is no active log file
            # and stderr is an actual terminal.
            return logfile is None and isatty(sys.stderr)
        return self.colorize

    def colored(self, logfile=None):
        return colored(enabled=self.supports_color(logfile))

    def get_task_logger(self, loglevel=None, name=None):
        logger = logging.getLogger(name or "celery.task.default")
        if loglevel is not None:
            logger.setLevel(loglevel)
        return logger

    def setup_logging_subsystem(self, loglevel=None, logfile=None,
            format=None, colorize=None, **kwargs):
        if Logging._setup:
            return
        format = format or self.format
        if colorize is None:
            colorize = self.supports_color(logfile)
        reset_multiprocessing_logger()
        if not is_py3k:
            ensure_process_aware_logger()
        receivers = signals.setup_logging.send(sender=None,
                        loglevel=loglevel, logfile=logfile,
                        format=format, colorize=colorize)
        if not receivers:
            root = logging.getLogger()

            if self.app.conf.CELERYD_HIJACK_ROOT_LOGGER:
                root.handlers = []

            for logger in filter(None, (root, get_multiprocessing_logger())):
                self._setup_logger(logger, logfile, format, colorize, **kwargs)
                if loglevel:
                    logger.setLevel(loglevel)
                signals.after_setup_logger.send(sender=None, logger=logger,
                                        loglevel=loglevel, logfile=logfile,
                                        format=format, colorize=colorize)
        Logging._setup = True

        return receivers

    def _detect_handler(self, logfile=None):
        """Create log handler with either a filename, an open stream
        or :const:`None` (stderr)."""
        logfile = sys.__stderr__ if logfile is None else logfile
        if hasattr(logfile, "write"):
            return logging.StreamHandler(logfile)
        return WatchedFileHandler(logfile)

    def get_default_logger(self, loglevel=None, name="celery"):
        """Get default logger instance.

        :keyword loglevel: Initial log level.

        """
        logger = logging.getLogger(name)
        if loglevel is not None:
            logger.setLevel(loglevel)
        return logger

    def setup_logger(self, loglevel=None, logfile=None,
            format=None, colorize=None, name="celery", root=True,
            app=None, **kwargs):
        """Setup the :mod:`multiprocessing` logger.

        If `logfile` is not specified, then `sys.stderr` is used.

        Returns logger object.

        """
        format = format or self.format
        if colorize is None:
            colorize = self.supports_color(logfile)

        if not root or self.app.conf.CELERYD_HIJACK_ROOT_LOGGER:
            return self._setup_logger(self.get_default_logger(loglevel, name),
                                      logfile, format, colorize, **kwargs)
        self.setup_logging_subsystem(loglevel, logfile,
                                     format, colorize, **kwargs)
        return self.get_default_logger(name=name)

    def setup_task_logger(self, loglevel=None, logfile=None, format=None,
            colorize=None, task_name=None, task_id=None, propagate=False,
            app=None, **kwargs):
        """Setup the task logger.

        If `logfile` is not specified, then `sys.stderr` is used.

        Returns logger object.

        """
        format = format or self.task_format
        if colorize is None:
            colorize = self.supports_color(logfile)

        logger = self._setup_logger(self.get_task_logger(loglevel, task_name),
                                    logfile, format, colorize, **kwargs)
        logger.propagate = int(propagate)    # this is an int for some reason.
                                             # better to not question why.
        signals.after_setup_task_logger.send(sender=None, logger=logger,
                                     loglevel=loglevel, logfile=logfile,
                                     format=format, colorize=colorize)
        return LoggerAdapter(logger, {"task_id": task_id,
                                      "task_name": task_name})

    def redirect_stdouts_to_logger(self, logger, loglevel=None,
            stdout=True, stderr=True):
        """Redirect :class:`sys.stdout` and :class:`sys.stderr` to a
        logging instance.

        :param logger: The :class:`logging.Logger` instance to redirect to.
        :param loglevel: The loglevel redirected messages will be logged as.

        """
        proxy = LoggingProxy(logger, loglevel)
        if stdout:
            sys.stdout = sys.__stdout__ = proxy
        if stderr:
            sys.stderr = sys.__stderr__ = proxy
        return proxy

    def _setup_logger(self, logger, logfile, format, colorize,
            formatter=ColorFormatter, **kwargs):

        if logger.handlers:  # Logger already configured
            return logger

        handler = self._detect_handler(logfile)
        handler.setFormatter(formatter(format, use_color=colorize))
        logger.addHandler(handler)
        return logger