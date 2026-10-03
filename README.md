# estim-camming

Control an output device from tips received on webcam streaming sites, with an
overlay for OBS Studio and a control panel with an emergency stop.

- **Modular**: streaming platforms, devices and waveform patterns are plugins
  (built-in or installed from other packages).
- **Safety first**: every output passes a safety guard (manual arming, level
  caps, soft start, emergency stop, stop on device error).
- **OBS overlay**: tip menu, current action, queue, output levels and recent
  tips as a Browser Source.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt      # or requirements-dev.txt for tests + docs
estim-camming init          # writes config.toml (simulator + dummy device)
estim-camming run           # open http://127.0.0.1:8765/control to arm
```

Documentation (user guide and design): **<https://cb-stimmer.github.io/estim-camming/>**,
published from [`docs/`](docs/index.md) on every push to `main`. In the desktop
window, **User guide** or **F1** opens it. Build it locally with:

```sh
pip install -r requirements-dev.txt
sphinx-build -b html docs docs/_build/html
```

Licensed under the BSD 2-Clause license.
