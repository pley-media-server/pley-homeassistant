# Changelog

## Unreleased

The first release of Pley for Home Assistant.

### Add a Pley server, and sign in with a code

Add Pley by its address, or from the server Home Assistant finds on your
network once Bonjour is on in Pley's Settings → Network. Home Assistant shows
a code to enter on Pley's link page, so your password never goes through it,
and it appears in Pley's list of signed-in apps as Pley for Home Assistant.
Before Home Assistant sends the server anything signed in, the server proves
it's the one you signed in to. When Pley stops accepting the sign-in, Home
Assistant asks you to sign in again, and removing the integration signs it
out. Its options choose which kinds of device are shown when they first
appear: TVs, unless you change it.

### See what plays on each device, and control it

Each device playing from Pley shows up as a media player, and so does each
TV and desktop app that's open and ready to play. It shows what plays, with
its artwork, and lets you play, pause, seek, skip and stop, as far as the app
takes them. A TV whose Pley app is open is idle, and one whose
app is closed is off, so automations can follow it. TVs are shown by default;
phones, tablets, computers and browsers are added disabled, for you to enable
the ones you want. On an administrator's account, other people's plays show
up too, with Stop only. Pley tells Home Assistant as things change, so
there's no waiting for a poll.
