# Quick start

This walkthrough uses the **simulator** (random fake tips) and the **dummy**
device (only logs), so it is completely safe.

1. **Create a config file**

   ```sh
   estim-camming init
   ```

   This writes `config.toml` in the current directory.

2. **Check it**

   ```sh
   estim-camming check
   ```

   This prints the device, platforms and tip menu, or explains what is wrong.

3. **Run it**

   ```sh
   estim-camming run
   ```

   The log shows the addresses of the control panel and the overlay:

   ```text
   WARNING estim_camming.app: output is DISARMED - arm it from the control panel when ready
   INFO    estim_camming.overlay.server: overlay (OBS browser source): http://127.0.0.1:8765/overlay
   INFO    estim_camming.overlay.server: control panel: http://127.0.0.1:8765/control
   ```

4. **Open the control panel** at <http://127.0.0.1:8765/control> and press
   **Arm output**. Simulated tips now play, and the log shows the dummy output
   levels. Try the **Send test tip** form, the master level slider, and **STOP**.

5. **Open the overlay** at <http://127.0.0.1:8765/overlay> to see what viewers
   will see, then add it to OBS ([OBS setup](obs-setup.md)).

6. Stop with **Ctrl+C**.

## Going live

1. Edit `config.toml` (see [Configuration](configuration.md)):
   - Replace the `simulator` platform with your streaming site, for example
     `chaturbate`.
   - Replace the `dummy` device with your device.
   - Adjust the tip menu (`[[rules]]`) and `[safety]` limits.
2. Run `estim-camming check`, then `estim-camming run`.
3. Follow the checklist in [Safety first](safety.md#before-every-show).
