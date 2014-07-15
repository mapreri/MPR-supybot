###
# Copyright (c) 2014, Mattia Rizzolo <mattia@mapreri.org>
# All rights reserved.
#
#
###

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks


class Debomatic(callbacks.Plugin):
    """This plugin should not "been used", it watches the known
    Debomatic istances and notify you if something is wrong."""
    threaded = True


Class = Debomatic


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
