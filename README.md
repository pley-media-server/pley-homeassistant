# <img src="https://raw.githubusercontent.com/pley-media-server/pley-homeassistant/master/custom_components/pley/brand/icon.png" alt="Pley" width="50" height="50"/> Pley for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/pley-media-server/pley-homeassistant)](https://github.com/pley-media-server/pley-homeassistant/releases)
[![Tests](https://github.com/pley-media-server/pley-homeassistant/actions/workflows/tests.yml/badge.svg)](https://github.com/pley-media-server/pley-homeassistant/actions/workflows/tests.yml)

Brings the devices that play from your [Pley](https://github.com/pley-media-server/pley)
media server into Home Assistant. Each TV and app shows what it plays as a
media player, with its artwork, and your dashboards and automations can
pause it, skip, stop it, or start a film, a playlist or a Live TV channel on
it.

## Use cases

- Dim the lights when a film starts on the living room TV, and bring them
  back up when it stops.
- Put the news on the kitchen TV at 7 every evening.
- Pause the TV when the doorbell rings.
- See on a dashboard what plays on every TV in the house, and, on Pley's
  administrator's profile, stop anyone's stream from there.

## Installation

The integration is installed as a custom component. HACS installs it and
tells you about updates; or you can install it by hand.

<details>
<summary>With HACS</summary>

1. Add this repository to HACS as a
   [custom repository](https://hacs.xyz/docs/faq/custom_repositories/), of
   type Integration:
   ```text
   https://github.com/pley-media-server/pley-homeassistant
   ```
   Or use this button:

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pley-media-server&repository=pley-homeassistant&category=integration)

1. Open **Pley** in HACS, and download it.
1. Restart Home Assistant.

</details>

<details>
<summary>By hand</summary>

Download `pley.zip` from the
[latest release](https://github.com/pley-media-server/pley-homeassistant/releases/latest),
and unzip it into `<config>/custom_components/pley`, where `<config>` is
your Home Assistant configuration folder. Then restart Home Assistant.

</details>

### Versions

Pley for Home Assistant needs Home Assistant 2026.9 or later, and a Pley
server recent enough to sign Home Assistant in: when it isn't, adding the
server says so, and a repair asks you to update Pley.

## Configuration

Add the integration in **Settings → Devices & services → Add integration**,
and choose **Pley**, or use this button:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=pley)

1. Enter the address you open Pley at in a browser, such as
   `192.168.1.20`, `pley.local` or `https://pley.example.com`. Without a
   port, a plain address goes to Pley's own, 7539. Plain http is only for a
   server on your local network: use https for one elsewhere.

   Home Assistant also finds Pley servers on your network, and offers them
   under **Settings → Devices & services → Discovered**, once **Found by
   Apple's devices (Bonjour)** is on in Pley's **Settings → Network**.
1. Home Assistant shows a code. On a phone or computer, open the address it
   shows (your server's `/link` page), sign in to Pley if it asks, enter the
   code and allow **Pley for Home Assistant**. Your password never goes
   through Home Assistant.

   Sign in as the profile whose devices you want to control. On an
   administrator's profile, Home Assistant also sees what everyone else
   plays, and can stop it.
1. That's it: the server and its devices appear.

The sign-in shows in Pley under **Account → Apps** as Pley for Home
Assistant, where you can sign it out. It may see your libraries (for
artwork), see what plays and stop it, and play on and control your
profile's devices. Before Home Assistant sends the server anything signed
in, the server proves it's the one you signed in to.

YAML configuration isn't supported.

### Options

**Configure** on the integration's page has one option:

Option | Description
-|-
Devices shown by default | The kinds of device whose media player starts enabled when Pley first sees it: TVs, phones, tablets, computers, web browsers and other devices. Default is TVs. The other kinds still get a media player, disabled until you enable it on the device's page. A change applies to devices Pley sees from then on: enable or disable those seen before on their own pages.

### Changing the server's address

If the server gets a new address, choose **Reconfigure** from the
integration's menu, and enter it. The server must prove it's the same one
before Home Assistant uses the new address. A server added by its IP address
and found on the network at a new one is moved there by itself, after the
same check. Pley makes that check with a key it can only hand out while it
can read its `secret.key`; a sign-in made without one is never moved by
itself, so change its address with **Reconfigure**.

## What you get

The Pley server is a device, and each Pley app that plays, or is open and
ready to play, is a device linked to it, with a media player. It's named as
the app names its device: the TV's name, "Pley for Kodi", "Firefox on
macOS".

**State.** A media player is **playing**, **paused** or **buffering**
while its device plays something; **idle** while one of your devices has
its Pley app open and ready to play what it's sent; and **off** while it
does neither, such as when the app is closed. Every Pley TV app (Apple TV,
Android TV, Kodi, Roku, Samsung, LG) and the desktop app can be idle; a
browser is on only while it plays. Pley can't turn a TV on or off.

**What plays.** The title, with its artwork and how far it has played; an
episode's show, season and number; a track's album and artist. A Live TV
channel shows its name, the programme on as the title and how far through
it is, and the next programme in the **Up next** attribute. The **Profile**
attribute says who plays, **Controls** what the player obeys from a remote,
**Plays when sent** what the device plays when it's sent something, and
**Playback** whether the server sends the file as it is or converts it.

**Your own devices** take what their Pley app obeys: play, pause, seek,
next and previous, and stop. Next and previous go to the next or previous
title, or change channel.

**Other people's devices** show up on an administrator's profile while they
play, with **Stop** only. Their player then says it was stopped by the
server's administrator.

**Kinds of device.** Only TVs start enabled, unless you change the option
above. Phones, tablets, computers, web browsers and others are added
disabled: enable the ones you want on their device pages. A device Pley
hasn't seen for 30 days is removed, and comes back if it plays again; one
Pley doesn't list now can also be deleted from its device page.

### Playing a title, a playlist or a channel

`media_player.play_media` sends one of your devices whose Pley app is open
(an idle, playing or paused TV) something to play, as its own **Play**
would. It plays at once, in place of whatever was playing. The ID says what
to play:

ID | Plays | Where to find its number
-|-|-
`pley://item/<id>` | A film, an episode, a track; a show or a season (its next episode); an album or an artist | The title's page in Pley's web app: `/item/42`
`pley://playlist/<id>` | A playlist, in order | The playlist's page: `/playlists/5`
`pley://channel/<id>` | A Live TV channel | The channel's page: the last number in `/live/a1b2c3/7`

Home Assistant asks for a `media_content_type` too; Pley goes by the ID, so
any type does. `extra` may also have:

Key | Value
-|-
`start_ms` | Where a title starts, in milliseconds.
`shuffle` | `true` plays a playlist, an album or an artist shuffled.
`audio_index` | A film's or an episode's audio track, by its stream index in the file.
`subtitle_index` | Its subtitles, by stream index; `-1` for none.

The TV says it's playing from Home Assistant. A channel plays only on a TV
whose app plays Live TV, for a profile allowed to watch it.

```yaml
action: media_player.play_media
target:
  entity_id: media_player.living_room_tv
data:
  media_content_type: movie
  media_content_id: pley://item/42
  extra:
    start_ms: 600000
```

## How it updates

Pley tells Home Assistant as soon as something starts, stops, pauses or
jumps, or an app opens or closes, so there's no waiting for a poll. In case
a message goes missing, Home Assistant also asks every 30 seconds while
something plays, and every 5 minutes when nothing does. When the connection
to Pley drops, it connects again and asks.

## Examples

Dim the living room lights when a film starts on the TV. Paste it into the
automation editor with **Edit in YAML** from its menu, or build it in the
visual editor with a **State** trigger.

```yaml
alias: Dim the lights for a film
triggers:
  - trigger: state
    entity_id: media_player.living_room_tv
    to: playing
conditions:
  - condition: state
    entity_id: media_player.living_room_tv
    attribute: media_content_type
    state: movie
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room
    data:
      brightness_pct: 20
```

Put the news channel on the kitchen TV at 7, if its Pley app is open. The
channel's number is the last one in its page's address in Pley's web app.

```yaml
alias: The news at 7
triggers:
  - trigger: time
    at: "19:00:00"
conditions:
  - condition: state
    entity_id: media_player.kitchen_tv
    state:
      - idle
      - playing
      - paused
actions:
  - action: media_player.play_media
    target:
      entity_id: media_player.kitchen_tv
    data:
      media_content_type: channel
      media_content_id: pley://channel/7
```

## Known limitations

- There's no media browser: titles, playlists and channels are played by
  their IDs from Pley's web app.
- Audio, subtitles and quality can't be changed from Home Assistant, only
  chosen as a title starts. Pley's own apps change them from their remotes.
- Home Assistant is a remote, not a player: Pley doesn't list it as a
  device to play on.
- A player that crashes, or loses its network, keeps showing what it played
  for up to 90 seconds, until Pley gives up on it.
- A browser is one device however many of its tabs play, and can't be sent
  something to play.
- Other people's Live TV isn't shown, only the titles they play.
- There's no seeking in a Live TV channel, even on a player that can pause
  it: the place shown comes from the guide, not the player.
- Pley can't turn a TV on, or open its app: a TV takes play_media only
  while its Pley app is open.

## Troubleshooting

- **Home Assistant doesn't find the server.** Pley announces itself on the
  network only once **Found by Apple's devices (Bonjour)** is on in its
  **Settings → Network**. Or add it by its address.
- **A repair says the Pley server needs updating.** The server is older than
  this version of the integration needs, so its devices aren't shown. Update
  Pley on the server: Home Assistant tries again every so often, and the
  repair goes away once it connects.
- **A repair says the Pley server isn't at its address.** Something answers
  at the server's address that can't prove it's your server: another Pley
  server, or another device that has the address now. Home Assistant sends
  it nothing. If the server has a new address, choose **Reconfigure** from
  the integration's menu and enter it. If the server was reinstalled with a
  new data folder, it's a new server: delete the integration's entry and add
  Pley again.
- **Home Assistant asks you to sign in again.** Pley no longer accepts its
  sign-in: it was signed out in Pley's **Account → Apps**, or the profile's
  password changed. Sign in again with a new code, as the same profile.
- **A device doesn't show up.** Only TVs start enabled. Find the device in
  **Settings → Devices & services → Pley**, and enable its media player. A
  TV shows up once its Pley app opens, or plays.
- **Something else is wrong.** Download the diagnostics from the
  integration's menu in **Settings → Devices & services → Pley**: they show
  what Pley lists, without your tokens, names or address. To see what
  happens in the log, enable debug logging from the same menu. Include both
  when you
  [open an issue](https://github.com/pley-media-server/pley-homeassistant/issues).

## Removal

1. Go to **Settings → Devices & services → Pley**, and delete the entry
   from its menu. Home Assistant signs out of Pley as it goes; if the server
   is out of reach then, sign it out in Pley's **Account → Apps**.
1. If you installed it with HACS, remove it in HACS. Otherwise, delete
   `<config>/custom_components/pley`.
1. Restart Home Assistant.

## Changelog

See [CHANGELOG.md](https://github.com/pley-media-server/pley-homeassistant/blob/master/CHANGELOG.md).

## Security

Home Assistant keeps a sign-in to your Pley server, which sees what plays
on your devices and controls them, so keep your backups safe. To report a
security problem, see
[SECURITY.md](https://github.com/pley-media-server/pley-homeassistant/blob/master/SECURITY.md).

## License

Apache License 2.0: see
[LICENSE](https://github.com/pley-media-server/pley-homeassistant/blob/master/LICENSE).
