#/usr/bin/env coffee

io = require('socket.io-client')

format = (host) ->
    "#{host}         "[0..10]

print_status = (host, what, msg) ->
    host = format(host)
    what = format(what)
    console.log(host, what, msg)

handle_webui_status = (host, socket, msg) ->
    socket.io.reconnection(false)
    print_status(host, "webui", msg)
    if "error" in msg
        socket.disconnect()

check = (host) ->
    socket = io.connect('http://debomatic-' + host + '.debian.net')

    socket.on 'b.status_debomatic', (data) ->
        msg = if data.running then 'running' else 'error'
        print_status(host, "debomatic", msg)
        socket.disconnect()
    socket.on 'connect', (data) ->
        handle_webui_status(host, socket, "running")
    socket.on 'connect_error', (data) ->
        handle_webui_status(host, socket, "error #{data.description}")
    socket.on 'connect_timeout', (data) ->
        handle_webui_status(host, socket, "error #{data.description}")

services = process.argv[2..]

if not services.length
    services = ['amd64', 'i386', 'powerpc', 'armel', 'armhf', 's390x']

check host for host in services
