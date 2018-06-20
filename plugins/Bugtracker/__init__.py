# -*- Encoding: utf-8 -*-
###
# Copyright (c) 2005-2007 Dennis Kaarsemaker
# Copyright (c) 2008-2010 Terence Simpson
# Copyright (c) 2017-     Krytarik Raido
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of version 2 of the GNU General Public License as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
###

"""
This plugin will display bug information when requested.
"""

import supybot
import supybot.world as world

from imp import reload

__version__ = "3.3.0"
__author__ = supybot.Author("Krytarik Raido", "krytarik", "krytarik@tuxgarage.com")
__contributors__ = {
    supybot.Author("Dennis Kaarsemaker", "Seveas", "dennis@kaarsemaker.net"): ['Original Author'],
    supybot.Author("Terence Simpson", "tsimpson", "tsimpson@ubuntu.com"): ['Original Author']
}
__url__ = 'https://launchpad.net/ubuntu-bots'

from . import config
reload(config)
from . import plugin
reload(plugin)

if world.testing:
    from . import test
Class = plugin.Class
configure = config.configure
