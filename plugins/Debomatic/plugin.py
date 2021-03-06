###
# Copyright (c) 2014, Mattia Rizzolo <mattia@mapreri.org>
# License: WTFPL
#
###

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

import threading
import subprocess
from time import sleep


class Debomatic(callbacks.Plugin):
    """You can invoke the bot with the "status" command, followed by a known
    service. Anyway it watches the known Debomatic istances and notify you if
    something is wrong."""
    threaded = True
    service_list = [
        'debomatic-amd64', 'webui-amd64',
        'debomatic-i386', 'webui-i386',
        'debomatic-arm64', 'webui-arm64',
        'debomatic-armel', 'webui-armel',
        'debomatic-armhf', 'webui-armhf',
        'debomatic-mips64el', 'webui-mips64el',
        'debomatic-mips', 'webui-mips',
        'debomatic-mipsel', 'webui-mipsel',
        'debomatic-powerpc', 'webui-powerpc',
        'debomatic-ppc64el', 'webui-ppc64el',
        'debomatic-s390x', 'webui-s390x',
    ]
    path = '/home/mattia/ircbot/plugins/Debomatic/checkstatus.coffee'

    def __init__(self, irc):
        self.__parent = super(Debomatic, self)
        self.__parent.__init__(irc)
        launch = threading.Thread(target=self._launcher, args=(irc))
        launch.start()

    def _launcher(self, irc, channel):
        down = []
        while True:
            status = self._do()
            for i in self.service_list:
                if not status[i] == "running":
                    if i not in down:
                        line = "WARNING!!! " + i + " is just went down! " + \
                               "Error messge: " + status[i]
                        msg = irc.privmsg(channel, line)
                        irc.queueMsg(msg)
                        down.append(i)
            for i in down:
                if status[i] == "running":
                    down.remove(i)
            sleep(30)

    def _do(self):
        L = []
        status = []
        statuses = {}
        try:
            out = subprocess.check_output(['coffee', self.path], timeout=30)
        except subprocess.TimeoutExpired as e:
            if e.output:
                out = e.output
            else:
                out = ''
        out = out.splitlines()
        for row in out:
            L.append(row)
        for row in L:
            item = {}
            row = row.decode().split()
            item['arch'] = row[0]
            item['service'] = row[1]
            item['status'] = ' '.join(row[2:])
            status.append(item)
        for i in status:
            service = i['service'] + '-' + i['arch']
            statuses[service] = i['status']
        return statuses

    def status(self, irc, msg, args, channel, name=None):
        """[service name]

        Show if the requested service is running
        """
        down = []
        avail = ", ".join([str(x) for x in self.service_list])
        if not name:
            status = self._do()
            for i in self.service_list:
                try:
                    if not status[i] == "running":
                        down.append(i)
                except KeyError:
                    down.append(i)
            if not down:
                msg = "Everything is up and running."
            else:
                msg = ", ".join([str(x) for x in down]) + " are down."
            irc.reply(msg)
        elif not [i for i in self.service_list if i == name]:
            msg = "There is no service named " + name + \
                  ". Available services include: " + avail + "."
            irc.reply(msg)
        else:
            status = self._do()
            try:
                if not status[name] == "running":
                    if not status[name]:
                        msg = name + " is down! Come on! (No error message)"
                    else:
                        msg = name + " is down! Come on! Error message: " + \
                              status[name]
                else:
                    msg = name + " is up and running!"
            except KeyError:
                msg = name + " is unreachable"
            irc.reply(msg)
    status = wrap(status, ['channel', optional('something')])

Class = Debomatic


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
