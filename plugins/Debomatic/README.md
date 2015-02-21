Supybot Debomatic Plugin
========================

This is a plugin for the IRC bot Supybot that constantly monitors some Debomatic
instances and notify you if something is wrong with them
Features:

* Notifies IRC channel of new instances went down
* Query instances status

Requirements
============

The `socket.io-client` node modules is required.
On Debian 8 (Jessie) onward:

```
sudo apt-get install --no-install-recommends npm nodejs-legacy
npm install socked.io-client
```
