# OBS setup

The overlay is a web page that OBS shows on top of your camera using a
**Browser Source**.

## Add the overlay

1. Start estim-camming (`estim-camming run`).
2. In OBS, in your scene's **Sources**, click **+** → **Browser**, and name it
   "Tip overlay".
3. Set:
   - **URL**: `http://127.0.0.1:8765/overlay`
   - **Width** / **Height**: `520` × `900` (adjust to taste)
   - Leave **Custom CSS** as is (the page background is transparent).
4. Position and resize the source in the preview.

## Show only some widgets

The overlay has five widgets: `menu` (tip menu), `current` (what is playing,
with progress), `queue`, `levels` (output bars), and `tips` (recent tips).
Choose them with the `widgets` parameter:

```text
http://127.0.0.1:8765/overlay?widgets=menu
http://127.0.0.1:8765/overlay?widgets=current,queue
```

Add one Browser Source per widget to place them independently, for example the
tip menu on the left and the current action at the bottom.

## Change the look

Paste CSS into the Browser Source's **Custom CSS** field:

```css
:root { --accent: #00d0ff; --bg: rgba(0, 0, 0, 0.6); --fg: #ffffff; --muted: #cccccc; }
body { font-size: 24px; }
```

## Control panel

Don't add the control panel to your scene. Open
`http://127.0.0.1:8765/control` in a normal browser window, or as an OBS
**Custom Browser Dock** (**Docks** → **Custom Browser Docks…**) so the STOP
button is always next to your OBS controls.

## Notes

- If estim-camming is restarted, the overlay reconnects by itself within a few
  seconds.
- If the overlay stays empty, right-click the source → **Refresh**, and check
  that the URL and port match `[overlay]` in your config.
