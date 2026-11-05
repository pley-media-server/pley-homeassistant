# Changelog

## 0.1.0 (2026-11-05)

The first release of Pley for Home Assistant. It needs Home Assistant 2026.9
or later, and installs from HACS as a custom repository: the
[README](README.md) says how, and has examples of automations.

### Add a Pley server, and sign in with a code

Add Pley by its address, or from the server Home Assistant finds on your
network once Bonjour is on in Pley's Settings → Network. Home Assistant shows
a code to enter on Pley's link page, so your password never goes through it,
and it appears in Pley's list of signed-in apps as Pley for Home Assistant.
Before Home Assistant sends the server anything signed in, the server answers
a challenge with a key only it and Home Assistant share. When Pley stops
accepting the sign-in, Home Assistant asks you to sign in again, and removing
the integration signs it out. Its options choose which kinds of device are
shown when they first appear: TVs, unless you change it.

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

### Play titles, playlists and channels on a TV

The `media_player.play_media` action sends a device whose Pley app is open a
title, a playlist or a Live TV channel to play, as its own Play would:
`pley://item/<id>`, `pley://playlist/<id>` or `pley://channel/<id>`, with the
ID from the web app's address for it. Its `extra` can say where a title
starts (`start_ms`), shuffle a playlist, an album or an artist (`shuffle`),
and choose a film's or an episode's audio and subtitles (`audio_index` and
`subtitle_index`). The TV says it's playing from Home Assistant.

### Fix what's wrong on the server's side, and change its address

When the Pley server is too old for the integration, or something other than
your server answers at its address, a repair in Settings → Repairs says
what's wrong and what to do, and goes away once it's fixed. If the server
gets a new address, change it with Reconfigure on the integration's page:
the server proves it's the same one before Home Assistant uses it. A device
Pley hasn't seen for 30 days is removed from Home Assistant, and comes back
if it plays again; one that isn't there now can also be deleted from its
device page.
