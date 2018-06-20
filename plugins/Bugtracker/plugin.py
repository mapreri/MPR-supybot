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

from supybot.commands import *
import supybot.utils as utils
import supybot.ircutils as ircutils
import supybot.ircdb as ircdb
import supybot.callbacks as callbacks
import supybot.conf as conf
import supybot.registry as registry
import supybot.log as supylog

import re, os, sys, time, json
import xml.dom.minidom as minidom
from email.parser import FeedParser
from pysimplesoap.client import SoapClient

def registerBugtracker(name, url='', description='', trackertype=''):
    conf.supybot.plugins.Bugtracker.bugtrackers().add(name)
    group       = conf.registerGroup(conf.supybot.plugins.Bugtracker.bugtrackers, name)
    URL         = conf.registerGlobalValue(group, 'url', registry.String(url, ''))
    DESC        = conf.registerGlobalValue(group, 'description', registry.String(description, ''))
    TRACKERTYPE = conf.registerGlobalValue(group, 'trackertype', registry.String(trackertype, ''))
    if url:
        URL.setValue(url)
    if description:
        DESC.setValue(description)
    if trackertype:
        if trackertype in defined_bugtrackers:
            TRACKERTYPE.setValue(trackertype)
        else:
            raise BugtrackerError("Unknown trackertype: %s" % trackertype)

def defaultIgnored(hostmask, recipient):
    if not conf.supybot.defaultIgnore():
        return False
    if conf.version <= '0.83.4.1' \
            and ircutils.isChannel(recipient):
        return False
    try:
        user = ircdb.users.getUser(hostmask)
    except KeyError:
        return True
    return False

def checkIgnored(hostmask, recipient):
    try:
        user = ircdb.users.getUser(hostmask)
        if user._checkCapability('owner'):
            return False
        elif user.ignore:
            return True
    except KeyError:
        pass
    if ircdb.ignores.checkIgnored(hostmask):
        return True
    if ircutils.isChannel(recipient):
        c = ircdb.channels.getChannel(recipient)
        if c.checkIgnored(hostmask):
            return True
    return False

def checkAddressed(text, channel):
    if channel:
        if text[0] in str(conf.supybot.reply.whenAddressedBy.chars.get(channel)):
            return True
    elif text[0] in conf.supybot.reply.whenAddressedBy.chars():
        return True
    return False

def _getnodetxt(node):
    L = []
    for childnode in node.childNodes:
        if childnode.nodeType == childnode.TEXT_NODE:
            L.append(childnode.data)
    if not L:
        raise ValueError("No text nodes")
    val = ''.join(L)
    if node.hasAttribute('encoding'):
        encoding = node.getAttribute('encoding')
        if encoding == 'base64':
            try:
                val = val.decode('base64')
            except:
                val = 'Cannot convert bug data from base64.'
    return utils.web.htmlToText(val, tagReplace='')

def _getnodeattr(node, attr):
    if node.hasAttribute(attr):
        val = node.getAttribute(attr)
    else:
        raise ValueError("No such attribute")
    return utils.web.htmlToText(val, tagReplace='')

class BugtrackerError(Exception):
    """A bugtracker error"""
    pass

class BugNotFoundError(Exception):
    """Pity, bug isn't there"""
    pass

cvere = re.compile(r'<th[^>]*>Description</th>.*?<td[^>]*>\s*(?P<cve>.*?)\s*</td>', re.I | re.DOTALL)
cverre = re.compile(r'<h2[^>]*>\s*(?P<cverr>.*?)\s*</h2>', re.I | re.DOTALL)
class Bugtracker(callbacks.PluginRegexp):
    """Show a link to a bug report with a brief description"""
    threaded = True
    callBefore = ('URL')
    regexps = ('turlSnarfer', 'bugSnarfer', 'cveSnarfer', 'oopsSnarfer')

    def __init__(self, irc):
        self.__parent = super(Bugtracker, self)
        self.__parent.__init__(irc)
        self.db = ircutils.IrcDict()
        for name in self.registryValue('bugtrackers'):
            registerBugtracker(name)
            group = self.registryValue('bugtrackers.%s' % name.replace('.','\\.'), value=False)
            if group.trackertype() in defined_bugtrackers:
                self.db[name] = defined_bugtrackers[group.trackertype()](name, group.url(), group.description(), group.trackertype())
            else:
                supylog.warning("Bugtracker: Unknown trackertype: %s (%s)" % (group.trackertype(), name))
        self.shorthand = utils.abbrev(list(self.db.keys()))
        self.shown = {}

    def is_ok(self, channel, tracker, bug):
        """Flood/repeat protection"""
        now = time.time()
        for k in list(self.shown.keys()):
            if self.shown[k] < now - self.registryValue('repeatdelay', channel):
                self.shown.pop(k)
        if (channel, tracker, bug) not in self.shown:
            self.shown[(channel, tracker, bug)] = now
            return True
        return False

    def add(self, irc, msg, args, name, trackertype, url, description):
        """<name> <type> <url> [<description>]

        Add a bugtracker to the list of defined bugtrackers. Currently supported types are
        Launchpad, Debbugs, Bugzilla, SourceForge, Github, GitLab, Gitea, Mantis, and Trac.
        <name> will be used to reference the bugtracker in all commands.
            Unambiguous abbreviations of it will also be accepted.
        <description> will be used to reference the bugtracker in the
            query result. If not given, it defaults to <name>.
        """
        name = name.lower()
        if not description:
            description = name
        if url[-1] == '/':
            url = url[:-1]
        trackertype = trackertype.lower()
        if trackertype in defined_bugtrackers:
            self.db[name] = defined_bugtrackers[trackertype](name, url, description, trackertype)
        else:
            irc.error("Bugtrackers of type '%s' are not understood" % trackertype)
            return
        registerBugtracker(name, url, description, trackertype)
        self.shorthand = utils.abbrev(list(self.db.keys()))
        irc.replySuccess()
    add = wrap(add, [('checkCapability', 'admin'), 'something', 'something', 'url', additional('text')])

    def remove(self, irc, msg, args, name):
        """<abbreviation>

        Remove the bugtracker associated with <abbreviation> from the list of
        defined bugtrackers.
        """
        try:
            name = self.shorthand[name.lower()]
            del self.db[name]
            self.registryValue('bugtrackers').remove(name)
            self.shorthand = utils.abbrev(list(self.db.keys()))
            irc.replySuccess()
        except KeyError:
            s = self.registryValue('replyNoBugtracker', msg.args[0] if ircutils.isChannel(msg.args[0]) else None)
            irc.error(s % name)
    remove = wrap(remove, [('checkCapability', 'admin'), 'text'])

    def rename(self, irc, msg, args, oldname, newname, newdesc):
        """<oldname> <newname>

        Rename the bugtracker associated with <oldname> to <newname>.
        """
        try:
            name = self.shorthand[oldname.lower()]
            group = self.registryValue('bugtrackers.%s' % name.replace('.','\\.'), value=False)
            d = group.description()
            if newdesc:
                d = newdesc
            self.db[newname] = defined_bugtrackers[group.trackertype()](name, group.url(), d)
            registerBugtracker(newname, group.url(), d, group.trackertype())
            del self.db[name]
            self.registryValue('bugtrackers').remove(name)
            self.shorthand = utils.abbrev(list(self.db.keys()))
            irc.replySuccess()
        except KeyError:
            s = self.registryValue('replyNoBugtracker', msg.args[0] if ircutils.isChannel(msg.args[0]) else None)
            irc.error(s % name)
    rename = wrap(rename, [('checkCapability', 'admin'), 'something', 'something', additional('text')])

    def list(self, irc,  msg, args, name):
        """[abbreviation]

        List defined bugtrackers. If [abbreviation] is specified, list the
        information for that bugtracker.
        """
        if name:
            name = name.lower()
            try:
                name = self.shorthand[name]
                (url, description, trackertype) = (self.db[name].url, self.db[name].description,
                                            self.db[name].__class__.__name__)
                irc.reply('%s: %s, %s [%s]' % (name, description, url, trackertype))
            except KeyError:
                s = self.registryValue('replyNoBugtracker', msg.args[0] if ircutils.isChannel(msg.args[0]) else None)
                irc.error(s % name)
        else:
            if self.db:
                L = list(self.db.keys())
                L.sort()
                irc.reply(utils.str.commaAndify(L))
            else:
                irc.reply('I have no defined bugtrackers.')
    list = wrap(list, [additional('text')])

    def inFilter(self, irc, msg):
        if not msg.prefix:
            return msg
        if not defaultIgnored(msg.prefix, msg.args[0]):
            return msg
        if checkIgnored(msg.prefix, msg.args[0]):
            return msg
        if msg.command == 'PRIVMSG':
            self.doPrivmsg(irc, msg)
        return msg

    def bugSnarfer(self, irc, msg, match):
        r"(?P<bt>[a-z][^\s:]*(\s+bugs?)?):*\s+#?(?P<bug>\d+(?!\d*[-.]\d+)(\s*([,\s]+|[,\s]*(and|und|en|et|ir|[&+]+))\s*#?\d+(?!\d*[-.]\d+))*)"
        channel = msg.args[0] if ircutils.isChannel(msg.args[0]) else None
        if checkAddressed(msg.args[1].strip(), channel):
            return
        if not self.registryValue('bugSnarfer', channel):
            return
        nbugs = msg.tagged('nbugs') or 0
        if nbugs >= 5:
            return

        bugids = re.split(r'[^\d]+', match.group('bug'))[:5-nbugs]

        # Begin HACK
        # Strings like "Ubuntu 1004" and "Ubuntu 1610" are false triggers for us
        if match.group('bt').lower() == 'ubuntu':
            bugids = [x for x in bugids if not re.match(r'^([4-9]|[12][0-9])(04|10)$', x)]
        # End HACK

        # Get tracker name
        bt = [x.lower() for x in match.group('bt').split()]
        sure_bug = bt[-1] in ('bug', 'bugs')

        bugids = list(set(bugids)) # remove dupes

        if not sure_bug:
            bugids = [x for x in bugids if int(x) > 100]

        msg.tag('nbugs', nbugs + len(bugids))

        name = ''
        showTracker = True
        if len(bt) == 1 and not sure_bug:
            try:
                name = bt[0]
                tracker = self.db[name]
            except:
                return
        elif len(bt) == 2:
            try:
                name = bt[0]
                tracker = self.db[name]
            except:
                name = ''

        if not name:
            showTracker = False
            snarfTarget = self.registryValue('snarfTarget', channel)
            if not snarfTarget:
                supylog.warning("Bugtracker: No snarfTarget set")
                return
            try:
                name = self.shorthand[snarfTarget.lower()]
                tracker = self.db[name]
            except:
                s = self.registryValue('replyNoBugtracker', name)
                irc.error(s % name)
                return

        for bugid in bugids:
            bugid = int(bugid)
            try:
                report = self.get_bug(channel or msg.nick, tracker, bugid, self.registryValue('showassignee', channel),
                                      self.registryValue('extended', channel), do_tracker=showTracker)
            except BugNotFoundError:
                if self.registryValue('replyWhenNotFound'):
                    irc.error("Could not find %s bug %d" % (tracker.description, bugid))
            except BugtrackerError as e:
                if not sure_bug and bugid < 30:
                    return
                irc.error(str(e))
            else:
                if report:
                    irc.reply(report)

    def turlSnarfer(self, irc, msg, match):
        r"(https?://)?((bugs\.debian\.org|pad\.lv)/|\S+/(show_bug\.cgi\?id=|bugreport\.cgi\?bug=|view\.php\?id=|bug=|bugs/|\+bug/|ticket/|feature-requests/|patches/|todo/|issues/|pulls?/|merge_requests/))(?P<bug>\d+)/?"
        channel = msg.args[0] if ircutils.isChannel(msg.args[0]) else None
        if checkAddressed(msg.args[1].strip(), channel):
            return
        if not self.registryValue('bugSnarfer', channel):
            return
        nbugs = msg.tagged('nbugs') or 0
        if nbugs >= 5:
            return
        msg.tag('nbugs', nbugs+1)
        url = match.group(0)
        bugid = int(match.group('bug'))
        if '://' in url:
            url = url[url.rfind('://')+3:]
        try:
            tracker = self.get_tracker(url, bugid)
            if not tracker:
                return
            report = self.get_bug(channel or msg.nick, tracker, bugid, self.registryValue('showassignee', channel),
                                  self.registryValue('extended', channel), do_url=False)
        except BugtrackerError as e:
            irc.error(str(e))
        except BugNotFoundError:
            if self.registryValue('replyWhenNotFound'):
                irc.error("Could not find %s bug %s" % (tracker.description, match.group('bug')))
        else:
            if report:
                irc.reply(report)

    # Only useful to Launchpad developers
    def oopsSnarfer(self, irc, msg, match):
        r"(https?://\S+[=/])?OOPS-(?P<oopsid>[\dA-Za-z]{6,})"
        channel = msg.args[0] if ircutils.isChannel(msg.args[0]) else None
        if checkAddressed(msg.args[1].strip(), channel):
            return
        if not self.registryValue('bugSnarfer', channel) or not self.registryValue('oopsSnarfer', channel):
            return
        oopsid = match.group('oopsid')
        if not self.is_ok(channel or msg.nick, 'lpoops', oopsid):
            return
        if not match.group(1):
            irc.reply('https://oops.canonical.com/?oopsid=OOPS-%s' % oopsid)

    def cveSnarfer(self, irc, msg, match):
        r"(https?://\S+=)?CVE[- ](?P<cveid>\d{4}[- ]\d{4,})"
        channel = msg.args[0] if ircutils.isChannel(msg.args[0]) else None
        if checkAddressed(msg.args[1].strip(), channel):
            return
        if not self.registryValue('bugSnarfer', channel) or not self.registryValue('cveSnarfer', channel):
            return
        cveid = match.group('cveid').replace(' ','-')
        if not self.is_ok(channel or msg.nick, 'cve', cveid):
            return
        url = 'https://cve.mitre.org/cgi-bin/cvename.cgi?name=CVE-%s' % cveid
        try:
            cvedata = utils.web.getUrl(url).decode('utf-8')
        except Exception as e:
            raise BugtrackerError('Could not get CVE data: %s (%s)' % (e, url))
        m = cvere.search(cvedata)
        if m:
            cve = utils.web.htmlToText(m.group('cve'), tagReplace='')
            if len(cve) > 380:
                cve = cve[:380] + '...'
            if not match.group(1):
                cve += ' <%s>' % url
            irc.reply(cve)
        else:
            m = cverre.search(cvedata)
            if m:
                cverr = utils.web.htmlToText(m.group('cverr'), tagReplace='')
                irc.reply(cverr)

    #TODO: As we will depend on launchpadlib, we should consider using lazr.uri.URI to do URL parsing
    def get_tracker(self, snarfurl, bugid):
        # SourceForge short domain
        snarfurl = snarfurl.replace('sf.net', 'sourceforge.net', 1)

        # Launchpad URL shortening
        snarfurl = re.sub(r'pad\.lv/(bug=)?(?P<bug>[0-9]+)', r'launchpad.net/bugs/\g<bug>', snarfurl)

        for t in list(self.db.keys()):
            tracker = self.db.get(t, None)
            if not tracker:
                supylog.error("No tracker for key '%s'" % t)
                continue
            url = tracker.url[tracker.url.rfind('://')+3:]
            if url in snarfurl:
                return tracker

        # No tracker found, bummer. Let's try and get one
        if 'show_bug.cgi' in snarfurl:
            tracker = Bugzilla().get_tracker(snarfurl)
        elif 'sourceforge.net' in snarfurl:
            tracker = SourceForge().get_tracker(snarfurl)
        elif 'github.com' in snarfurl:
            tracker = GitHub().get_tracker(snarfurl)
        elif re.match(r'[^\s/]+/[^\s/]+/[^\s/]+/(issues|pulls|merge_requests)', snarfurl):
            tracker = GitLab().get_tracker(snarfurl, bugid)
            if not tracker:
                tracker = Gitea().get_tracker(snarfurl, bugid)
        elif 'view.php' in snarfurl:
            tracker = Mantis().get_tracker(snarfurl)
        elif '/ticket/' in snarfurl:
            tracker = Trac().get_tracker(snarfurl)
        else:
            return

        if tracker:
            self.db[tracker.name] = tracker
            self.shorthand = utils.abbrev(list(self.db.keys()))
            return tracker

    def get_bug(self, channel, tracker, id, do_assignee, do_extinfo, do_url=True, do_tracker=True):
        if not self.is_ok(channel, tracker, id):
            return

        bugdata = tracker.get_bug(id)
        if not bugdata:
            return

        (bid, product, title, severity, status, assignee, url, extinfo, duplicate) = bugdata

        if duplicate and not self.is_ok(channel, tracker, bid):
            return

        bugtype = re.match(r'.*/(feature-)?(?P<type>request|patch|todo|issue|pull|merge|ticket)(_requests)?(e?s)?/[0-9]+/?$', url)
        if do_tracker and tracker.trackertype not in ('github', 'gitlab', 'gitea'):
            if re.match(r'.*/(bugs|feature-requests|patches|todo|issues|pulls?|merge_requests|ticket)/?$', tracker.description):
                report = '%s %d' % (tracker.description, bid)
            else:
                if bugtype:
                    report = '%s %s %d' % (tracker.description, bugtype.group('type'), bid)
                else:
                    report = '%s bug %d' % (tracker.description, bid)
        else:
            if bugtype:
                report = '%s %d' % (bugtype.group('type').title(), bid)
            else:
                report = 'Bug %d' % bid

        if product:
            report += ' in %s' % product

        report += ' "%s"' % title.replace('"', "'").strip()

        if do_extinfo and extinfo:
            report += ' (%s)' % ', '.join(extinfo)

        if do_assignee and assignee:
            report += ' (assigned: %s)' % assignee

        severity_status = []
        if severity:
            severity_status.append(' '.join(word[0].upper() + word[1:].lower() for word in severity.split()))
        severity_status.append(' '.join(word[0].upper() + word[1:].lower() for word in status.split()))
        report += ' [%s]' % ', '.join(severity_status)

        if duplicate:
            report += ' [duplicate: %s]' % duplicate[0]

        if do_url:
            report += ' %s' % url

        message_max = 450 - len(channel)
        if len(report) > message_max:
            report_parts = report.split('"')
            report_start = report_parts[0]
            report_end = report_parts[-1]
            report_title = '"'.join(report_parts[1:-1])
            title_max = message_max - len(report_start) - len(report_end) - 5
            report_title_cut = report_title[:title_max].rsplit(None, 1)[0] + '...'
            report = '%s"%s"%s' % (report_start, report_title_cut, report_end)

        return report

# Define all bugtrackers
class IBugtracker:
    def __init__(self, name=None, url=None, description=None, trackertype=None):
        self.name        = name
        self.url         = url
        self.description = description
        self.trackertype = trackertype
        self.errget      = 'Could not get data from %s: %s (%s)'
        self.errparse    = 'Could not parse data from %s: %s (%s)'

    def get_bug(self, id):
        raise BugTrackerError("Bugtracker class does not implement get_bug")

    def get_tracker(self, url):
        raise BugTrackerError("Bugtracker class does not implement get_tracker")

    def __str__(self):
        return '%s(%s)' % (self.__class__.__name__, self.url)

    def __hash__(self):
        return hash(self.url)

    def __cmp__(self, other): # used implicitly in Bugtracker.is_ok()
        return cmp(hash(self), hash(other))

    def __str__(self):
        return self.name

class Bugzilla(IBugtracker):
    def get_tracker(self, url):
        try:
            match = re.match(r'(?P<url>(?P<name>[^\s/]+).*)/show_bug\.cgi', url)
            name  = desc = match.group('name')
            url   = 'https://%s' % match.group('url')
#            registerBugtracker(name, url, desc, 'bugzilla')
            return Bugzilla(name, url, desc, 'bugzilla')
        except:
            pass

    def get_bug(self, id):
        url = "%s/rest/bug/%d" % (self.url, id)
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))['bugs'][0]
        except Exception as e:
            # For old-stable Bugzilla
            if 'HTTP Error 404' in str(e):
                return self.get_bug_old(id)
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            status = bug['status']
            if bug['resolution']:
                status += ': %s' % bug['resolution']
            if bug['assigned_to_detail']:
                assignee = bug['assigned_to_detail']['real_name']
                if not assignee:
                    assignee = bug['assigned_to_detail']['name']
            else:
                assignee = ''
            return (id, bug['product'], bug['summary'], bug['severity'], status, assignee,
                    "%s/show_bug.cgi?id=%d" % (self.url, id), [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

    def get_bug_old(self, id): # Deprecated
        url = "%s/show_bug.cgi?id=%d&ctype=xml" % (self.url, id)
        try:
            bugxml = utils.web.getUrl(url)
            zilladom = minidom.parseString(bugxml)
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        bug_n = zilladom.getElementsByTagName('bug')[0]
        if bug_n.hasAttribute('error'):
            errtxt = bug_n.getAttribute('error')
            if errtxt == 'NotFound':
                raise BugNotFoundError
            s = 'Error getting %s bug #%d: %s' % (self.description, id, errtxt)
            raise BugtrackerError(s)
        try:
            title = _getnodetxt(bug_n.getElementsByTagName('short_desc')[0])
            status = _getnodetxt(bug_n.getElementsByTagName('bug_status')[0])
            try:
                status = "%s: %s" % (status, _getnodetxt(bug_n.getElementsByTagName('resolution')[0]))
            except:
                pass
            product = _getnodetxt(bug_n.getElementsByTagName('product')[0])
            severity = _getnodetxt(bug_n.getElementsByTagName('bug_severity')[0])
            try:
                assignee = _getnodeattr(bug_n.getElementsByTagName('assigned_to')[0], 'name')
            except:
                try:
                    assignee = _getnodetxt(bug_n.getElementsByTagName('assigned_to')[0])
                except:
                    assignee = ''
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))
        return (id, product, title, severity, status, assignee, "%s/show_bug.cgi?id=%d" % (self.url, id), [], [])

class Launchpad(IBugtracker):
    statuses = ("Unknown", "Invalid", "Opinion", "Won't Fix", "Fix Released", "Fix Committed", "New",
                "Incomplete", "Confirmed", "Triaged", "In Progress")
    severities = ("Unknown", "Undecided", "Wishlist", "Low", "Medium", "High", "Critical")

    def __init__(self, *args, **kwargs):
        IBugtracker.__init__(self, *args, **kwargs)
        self.lp = None

        # A word to the wise:
        # The Launchpad API is much better than the /+text interface we currently use,
        # it's faster and easier to get the information we need.
        # The current /+text interface is not really maintained by Launchpad and most,
        # or all, of the Launchpad developers hate it. For this reason, we are dropping
        # support for /+text in the future in favour of launchpadlib.
        # Terence Simpson (tsimpson) 2010-04-20

        try:
            from launchpadlib.launchpad import Launchpad
            cachedir = os.path.join(conf.supybot.directories.data.tmp(), 'launchpadlib')
            self.lp = Launchpad.login_anonymously("Ubuntu Bots - Bugtracker", 'production', cachedir, version='devel')
        except ImportError:
            supylog.warning("Please install python-launchpadlib, the old interface is deprecated")
        except Exception:
            self.lp = None
            supylog.exception("Unknown exception while accessing the Launchpad API")

    def _parse(self, task): # Deprecated
        parser = FeedParser()
        parser.feed(task)
        return parser.close()

    @classmethod
    def _rank(cls, task):
        try:
            return float('%d.%02d' % (cls.statuses.index(task.status),
                         cls.severities.index(task.importance)))
        except:
            return 0

    @classmethod
    def _rank_old(cls, task):
        try:
            return float('%d.%02d' % (cls.statuses.index(task['status']),
                         cls.severities.index(task['importance'])))
        except:
            return 0

    @classmethod
    def _sort(cls, task1, task2): # Deprecated
        try:
            if task1.status != task2.status:
                if cls.statuses.index(task1.status) < cls.statuses.index(task2.status):
                    return -1
                return 1

            if task1.importance != task2.importance:
                if cls.severities.index(task1.importance) < cls.severities.index(task2.importance):
                    return -1
                return 1
        except:
            return 0
        return 0

    @classmethod
    def _sort_old(cls, task1, task2): # Deprecated
        try:
            if task1['status'] != task2['status']:
                if cls.statuses.index(task1['status']) < cls.statuses.index(task2['status']):
                    return -1
                return 1

            if task1['importance'] != task2['importance']:
                if cls.severities.index(task1['importance']) < cls.severities.index(task2['importance']):
                    return -1
                return 1
        except:
            return 0
        return 0

    def get_bug(self, id): #TODO: Remove this method and rename 'get_bug_new' to 'get_bug'
        if self.lp:
            return self.get_bug_new(id)
        return self.get_bug_old(id)

    def get_bug_new(self, id): #TODO: Rename this method to 'get_bug'
        try:
            bugdata = self.lp.bugs[id]
            if bugdata.private:
                raise BugtrackerError("This bug is private")
            duplicate = []
            dup = bugdata.duplicate_of
            while dup:
                duplicate.append(str(bugdata.id))
                bugdata = dup
                dup = bugdata.duplicate_of

            extinfo = ['affected: %d' % bugdata.users_affected_count_with_dupes]
            extinfo.append('heat: %d' % bugdata.heat)
            tasks = bugdata.bug_tasks

            if tasks.total_size > 1:
                taskdata = sorted(tasks, key=self._rank)[-1]
            else:
                taskdata = tasks[0]

            if taskdata.assignee:
                assignee = taskdata.assignee.display_name
            else:
                assignee = ''

        except Exception as e:
            if type(e).__name__ == 'HTTPError': # messy, but saves trying to import lazr.restfulclient.errors.HTPError
                if e.response.status == 404:
                    bugNo = e.content.split()[-1][2:-1] # extract the real bug number
                    if bugNo != str(id): # A duplicate of a private bug, at least we know it exists
                        raise BugtrackerError('Bug #%d is a duplicate of bug #%s, but it is private (%s/bugs/%s)' % (id, bugNo, self.url, bugNo))
                    raise BugtrackerError("Bug #%d is private or does not exist (%s/bugs/%d)" % (id, self.url, id)) # Could be private, could just not exist
                raise BugtrackerError(self.errget % (self.description, e, '%s/bugs/%d' % (self.url, id)))
            elif isinstance(e, KeyError):
                raise BugNotFoundError
            raise BugtrackerError(self.errget % (self.description, e, '%s/bugs/%d' % (self.url, id)))

        return (bugdata.id, taskdata.bug_target_display_name, bugdata.title, taskdata.importance, taskdata.status,
                assignee, "%s/bugs/%d" % (self.url, bugdata.id), extinfo, duplicate)

    def get_bug_old(self, id, duplicate=None): # Deprecated
        try:
            bugdata = utils.web.getUrl("%s/bugs/%d/+text" % (self.url, id)).decode('utf-8')
        except Exception as e:
            if 'HTTP Error 404' in str(e):
                if duplicate:
                    raise BugtrackerError('Bug #%d is a duplicate of bug #%d, but it is private (%s/bugs/%d)' % (duplicate, id, self.url, id))
                else:
                    raise BugNotFoundError
            raise BugtrackerError(self.errget % (self.description, e, '%s/bugs/%d' % (self.url, id)))

        try:
            # Split bug data into separate pieces (bug data, task data)
            data    = bugdata.split('\n\nContent-Type:', 1)[0].split('\n\n')
            bugdata = self._parse(data[0])
            if not bugdata['duplicate-of']:
                taskdata = list(map(self._parse, data[1:]))
                if len(taskdata) > 1:
                    taskdata = sorted(taskdata, key=self._rank_old)[-1]
                else:
                    taskdata = taskdata[0]
                if taskdata['assignee']:
                    assignee = re.sub(r' \([^)]*\)$', '', taskdata['assignee'])
                else:
                    assignee = ''
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, '%s/bugs/%d' % (self.url, id)))

        # Try and find duplicates
        if bugdata['duplicate-of']:
            data = self.get_bug_old(int(bugdata['duplicate-of']), duplicate or id)
            data[8].append(bugdata['bug'])
            return data

        return (id, taskdata['task'], bugdata['title'], taskdata['importance'], taskdata['status'],
                assignee, "%s/bugs/%d" % (self.url, id), [], [])

# <rant>
# Debbugs sucks donkeyballs
# * HTML pages are inconsistent
# * Parsing mboxes gets incorrect with cloning perversions (eg with bug 330000)
# * No sane way of accessing bug reports in a machine readable way (bts2ldap
#   has no search on bugid)
# * The damn thing allow incomplete bugs, eg bugs without severity set. WTF?!?
#
# Fortunately bugs.donarmstrong.com has a SOAP interface which we can use.
# </rant>
class Debbugs(IBugtracker):
    def __init__(self, *args, **kwargs):
        IBugtracker.__init__(self, *args, **kwargs)
        self.soap_client = SoapClient("%s/cgi-bin/soap.cgi" % self.url, namespace="Debbugs/SOAP")

    def get_bug(self, id):
        url = "%s/cgi-bin/bugreport.cgi?bug=%d" % (self.url, id)
        try:
            raw = self.soap_client.get_status(bugs=id)
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        if not hasattr(raw, 'item'):
            raise BugNotFoundError
        try:
            raw = raw.item.value
            if str(raw.fixed_versions):
                status = 'Fixed'
            else:
                status = 'Open'
            return (id, str(raw.package), str(raw.subject), str(raw.severity), status, '', "%s/%d" % (self.url, id), [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

class SourceForge(IBugtracker):
    def get_tracker(self, url):
        try:
            match = re.match(r'sourceforge\.net/p/[^\s/]+/(bugs|feature-requests|patches|todo)', url)
            name  = desc = match.group(0)
            url   = 'https://%s' % name
#            registerBugtracker(name, url, desc, 'sourceforge')
            return SourceForge(name, url, desc, 'sourceforge')
        except:
            pass

    def get_bug(self, id):
        url = "%s/%d/" % (self.url.replace('sourceforge.net', 'sourceforge.net/rest'), id)
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))['ticket']
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            product = severity = ''
            if bug['labels']:
                product = bug['labels'][0]
            if '_priority' in bug['custom_fields']:
                severity = 'Pri: %s' % bug['custom_fields']['_priority']
            return (id, product, bug['summary'], severity, ': '.join(bug['status'].split('-')),
                     bug['assigned_to'], "%s/%d/" % (self.url, id), [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

class GitHub(IBugtracker):
    def get_tracker(self, url):
        try:
            match = re.match(r'github\.com/[^\s/]+/[^\s/]+/(issues|pulls?)', url)
            name  = desc = match.group(0)
            url   = 'https://%s' % name
            # Pulls are inconsistent in main and single page URLs
            name  = desc = re.sub(r'/pull$', r'/pulls', name)
#            registerBugtracker(name, url, desc, 'github')
            return GitHub(name, url, desc, 'github')
        except:
            pass

    def get_bug(self, id):
        url = "%s/%d" % (self.url.replace('github.com', 'api.github.com/repos'), id)
        # Pulls are inconsistent in web and API URLs
        url = url.replace('/pull/', '/pulls/')
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            product = '/'.join(url.split('/')[-4:-2])
            if 'merged' in bug and bug['merged']:
                status = 'Merged'
            else:
                status = bug['state']
            if bug['assignee']:
                assignee = bug['assignee']['login']
            else:
                assignee = ''
            return (id, product, bug['title'], '', status, assignee, bug['html_url'], [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

class GitLab(IBugtracker):
    def get_tracker(self, url, id):
        try:
            match = re.match(r'[^\s/]+/[^\s/]+/[^\s/]+/(issues|merge_requests)', url)
            name  = desc = match.group(0)
            url   = 'https://%s' % name
            bugurl = '%s/%d.json' % (url, id)
            bugjson = utils.web.getUrl(bugurl)
            bug   = json.loads(bugjson.decode('utf-8'))
#            registerBugtracker(name, url, desc, 'gitlab')
            return GitLab(name, url, desc, 'gitlab')
        except:
            pass

    def get_bug(self, id):
        url = "%s/%d.json" % (self.url, id)
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            product = '/'.join(url.split('/')[-4:-2])
            status = bug['state']
            if 'assignees' in bug and bug['assignees']:
                assignee = bug['assignees'][0]['name']
            else:
                assignee = ''
            return (id, product, bug['title'], '', status, assignee, "%s/%d" % (self.url, id), [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

class Gitea(IBugtracker):
    def get_tracker(self, url, id):
        try:
            match = re.match(r'[^\s/]+/[^\s/]+/[^\s/]+/(issues|pulls)', url)
            name  = desc = match.group(0)
            url   = 'https://%s' % name
            bugurl = '%s/%d' % (re.sub(r'://[^\s/]+/', r'\g<0>api/v1/repos/', url), id)
            bugjson = utils.web.getUrl(bugurl)
            bug   = json.loads(bugjson.decode('utf-8'))
#            registerBugtracker(name, url, desc, 'gitea')
            return Gitea(name, url, desc, 'gitea')
        except:
            pass

    def get_bug(self, id):
        url = "%s/%d" % (re.sub(r'://[^\s/]+/', r'\g<0>api/v1/repos/', self.url), id)
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))
        except Exception as e:
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            product = '/'.join(url.split('/')[-4:-2])
            if 'merged' in bug and bug['merged']:
                status = 'Merged'
            else:
                status = bug['state']
            if bug['assignee']:
                assignee = bug['assignee']['username']
            else:
                assignee = ''
            # Issues have no 'html_url', but pulls do
            return (id, product, bug['title'], '', status, assignee, "%s/%d" % (self.url, id), [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

class Mantis(IBugtracker):
    def __init__(self, *args, **kwargs):
        IBugtracker.__init__(self, *args, **kwargs)
        self.soap_client = SoapClient("%s/api/soap/mantisconnect.php" % self.url, namespace="http://futureware.biz/mantisconnect")

    def get_tracker(self, url):
        try:
            match = re.match(r'(?P<url>(?P<name>[^\s/]+).*)/view\.php', url)
            name  = desc = match.group('name')
            url   = 'https://%s' % match.group('url')
#            registerBugtracker(name, url, desc, 'mantis')
            return Mantis(name, url, desc, 'mantis')
        except:
            pass

    def get_bug(self, id):
        url = "%s/api/rest/issues/%d" % (self.url, id)
        try:
            bugjson = utils.web.getUrl(url)
            bug = json.loads(bugjson.decode('utf-8'))['issues'][0]
        except Exception as e:
            # REST API may not be enabled yet
            if 'HTTP Error 404' in str(e):
                return self.get_bug_old(id)
            raise BugtrackerError(self.errget % (self.description, e, url))
        try:
            return (id, bug['project']['name'], bug['summary'], bug['severity']['name'], bug['resolution']['name'], '', url, [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

    def get_bug_old(self, id): # Deprecated
        url = "%s/view.php?id=%d" % (self.url, id)
        try:
            raw = self.soap_client.mc_issue_get(username='', password='', issue_id=id)
        except Exception as e:
            if 'Issue #%d not found' % id in str(e):
                raise BugNotFoundError
            # Often SOAP is not enabled
            if '.' in self.name:
                supylog.exception(self.errget % (self.description, e, url))
                return
            raise BugtrackerError(self.errget % (self.description, e, url))
        if not hasattr(raw, 'id'):
            raise BugNotFoundError
        try:
            return (id, str(raw.project.name), str(raw.summary), str(raw.severity.name), str(raw.resolution.name), '', url, [], [])
        except Exception as e:
            raise BugtrackerError(self.errparse % (self.description, e, url))

# For Trac-based trackers we get the tab-separated-values format.
# The other option is a comma-separated-values format, but if the description
# has commas, things get tricky.
# This should be more robust than the screen scraping done previously.
class Trac(IBugtracker):
    def get_tracker(self, url):
        try:
            match = re.match(r'(?P<name>[^\s/]+).*/ticket', url)
            name  = desc = match.group('name')
            url   = 'https://%s' % match.group(0)
#            registerBugtracker(name, url, desc, 'trac')
            return Trac(name, url, desc, 'trac')
        except:
            pass

    def get_bug(self, id): # This is still a little rough, but it works :)
        url = "%s/%d" % (self.url, id)
        try:
            raw = utils.web.getUrl("%s?format=tab" % url).decode('utf-8')
        except Exception as e:
            # Due to unreliable matching
            if '.' in self.name:
                supylog.exception(self.errget % (self.description, e, url))
                return
            if 'HTTP Error 500' in str(e):
                raise BugNotFoundError
            raise BugtrackerError(self.errget % (self.description, e, url))
        raw = raw.replace('\r\n', '\n')
        (headers, rest) = raw.split('\n', 1)
        headers = headers.strip().split('\t')
        rest = rest.strip().split('\t')

        title = status = package = severity = assignee = ""
        if "summary" in headers:
            title = rest[headers.index("summary")]
        if "status" in headers:
            status = rest[headers.index("status")]
        if "component" in headers:
            package = rest[headers.index("component")]
        if "severity" in headers:
            severity = rest[headers.index("severity")]
        elif "priority" in headers:
            severity = rest[headers.index("priority")]
        if "owner" in headers:
            assignee = rest[headers.index("owner")]
        return (id, package, title, severity, status, assignee, url, [], [])

# Introspection is quite cool
defined_bugtrackers = {}
v = vars()
for k in list(v.keys()):
    if type(v[k]) == type(IBugtracker) and issubclass(v[k], IBugtracker) and not (v[k] == IBugtracker):
        defined_bugtrackers[k.lower()] = v[k]

registerBugtracker('mozilla', 'https://bugzilla.mozilla.org', 'Mozilla', 'bugzilla')
registerBugtracker('gnome', 'https://bugzilla.gnome.org', 'Gnome', 'bugzilla')
registerBugtracker('gnome2', 'https://bugs.gnome.org', 'Gnome', 'bugzilla')
registerBugtracker('kde', 'https://bugs.kde.org', 'KDE', 'bugzilla')
registerBugtracker('xfce', 'https://bugzilla.xfce.org', 'Xfce', 'bugzilla')
registerBugtracker('lxde', 'https://sourceforge.net/p/lxde/bugs', 'LXDE', 'sourceforge')
registerBugtracker('freedesktop', 'https://bugzilla.freedesktop.org', 'Freedesktop', 'bugzilla')
registerBugtracker('freedesktop2', 'https://bugs.freedesktop.org', 'Freedesktop', 'bugzilla')
registerBugtracker('openoffice', 'https://bz.apache.org/ooo', 'OpenOffice', 'bugzilla')
registerBugtracker('ubuntu', 'https://launchpad.net', 'Ubuntu', 'launchpad')
registerBugtracker('ubottu', 'https://launchpad.net', 'Ubottu', 'launchpad')
registerBugtracker('launchpad', 'https://launchpad.net', 'Launchpad', 'launchpad')
registerBugtracker('lp', 'https://launchpad.net', 'Launchpad', 'launchpad')
registerBugtracker('debian', 'https://bugs.debian.org', 'Debian', 'debbugs')
registerBugtracker('supybot', 'https://sourceforge.net/p/supybot/bugs', 'Supybot', 'sourceforge')
registerBugtracker('irssi', 'https://github.com/irssi/irssi/issues', 'irssi/irssi', 'github')
registerBugtracker('mantis', 'https://www.mantisbt.org/bugs', 'Mantis', 'mantis')
registerBugtracker('trac', 'https://trac.edgewall.org/ticket', 'Trac', 'trac')
registerBugtracker('pidgin', 'https://developer.pidgin.im/ticket', 'Pidgin', 'trac')
Class = Bugtracker
