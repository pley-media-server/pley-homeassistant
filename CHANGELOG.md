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
